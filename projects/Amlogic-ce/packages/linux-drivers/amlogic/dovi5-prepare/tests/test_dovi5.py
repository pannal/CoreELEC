#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / 'scripts' if (HERE.parent / 'scripts/dv5_patch.py').is_file() else HERE
sys.path.insert(0, str(SCRIPTS))
import dv5_patch as p
import dovi5_prepare as prep

fixture_parser = argparse.ArgumentParser(add_help=False)
fixture_parser.add_argument('--fixture', type=Path, default=os.environ.get('DOVI5_TEST_SOURCE'))
fixture_parser.add_argument('--reference', type=Path, action='append', help='external built or installed native shim; repeat to check each artifact')
fixture_parser.add_argument('--versions', type=Path, help='matching actual kernel Module.symvers')
fixture_parser.add_argument('--exports', type=Path, help='matching System.map (host exports) or captured kallsyms')
fixture_parser.add_argument('--reference-report', type=Path, help='write actual artifact hashes and results outside the source tree')
fixture_parser.add_argument('--loader', type=Path, default=os.environ.get('DOVI5_TEST_LOADER'))
fixture_args, unittest_args = fixture_parser.parse_known_args()
BLOB = fixture_args.fixture
if BLOB is not None and not BLOB.is_file():
    raise SystemExit('Explicit Dolby module test fixture is not a regular file')
REFERENCES = fixture_args.reference or []
if REFERENCES or fixture_args.versions or fixture_args.exports or fixture_args.reference_report:
    if not (REFERENCES and fixture_args.versions and fixture_args.exports and BLOB):
        raise SystemExit('Actual-reference tests require --fixture, --reference, --versions and --exports together')
    for path in [*REFERENCES, fixture_args.versions, fixture_args.exports]:
        if not path.is_file(): raise SystemExit('External target input is not a file: ' + str(path))
LOADER = fixture_args.loader
if LOADER is None:
    for ancestor in HERE.parents:
        candidate = ancestor / 'projects/Amlogic-ce/packages/linux-drivers/amlogic/opentee_linuxdriver/scripts/dovi-loader.sh'
        if candidate.is_file():
            LOADER = candidate
            break
PROFILE = json.loads((SCRIPTS / 'canary-profile.json').read_bytes())
SHIM_EXPORTS = {'register_dv5shim_func', 'unregister_dv5shim_func', 'dv5_stack_chk_guard',
                'get_cpu_type_from_media', '_printk', '__cfi_slowpath_diag',
                '__ubsan_handle_cfi_check_fail_abort', '__stack_chk_fail'}
KERNEL_EXPORTS = {'module_layout', 'memcpy', 'memset', 'strlen', 'snprintf', 'sprintf',
                  'vmalloc', 'vfree', 'get_meson_cpu_version', 'dump_stack'}


def reference_fixture():
    # Independently encode a small native module reference, without using production ELF.encode.
    names = ['', '.text', '.rodata', '.modinfo', '.gnu.linkonce.this_module',
             '.rela.gnu.linkonce.this_module', '__versions', '.symtab', '.strtab', '.shstrtab']
    data = [b'', bytes(8), struct.pack('<II', 24, 56),
            b'vermagic=4.9.269 SMP preempt mod_unload modversions aarch64\0name=dv_compat_shim\0',
            bytes(24) + b'dv_compat_shim\0' + bytes(832 - 24 - len(b'dv_compat_shim\0')), b'',
            struct.pack('<Q', 0x12345678) + b'module_layout' + bytes(56 - len('module_layout')), b'', b'', b'']
    # NULL, one local .text SECTION, then global symbols and exports.
    symnames = ['', '', 'init_module', 'cleanup_module', '__this_module',
                'dv5_module_name_offset', 'dv5_module_name_size'] + ['__ksymtab_' + n for n in sorted(SHIM_EXPORTS)]
    strings = bytearray(b'\0')
    symbols = []
    for i, name in enumerate(symnames):
        off = 0
        if name:
            off = len(strings)
            strings.extend(name.encode() + b'\0')
        if i == 0:
            entry = (off, 0, 0, 0, 0, 0)
        elif i == 1:
            entry = (off, 3, 0, 1, 0, 0)
        elif i in (2, 3):
            entry = (off, 0x12, 0, 1, (i - 2) * 4, 4)
        elif i == 4:
            entry = (off, 0x11, 0, 4, 0, 832)
        elif i in (5, 6):
            entry = (off, 0x11, 0, 2, (i - 5) * 4, 4)
        else:
            entry = (off, 0x11, 0, 2, 0, 4)
        symbols.append(struct.pack('<IBBHQQ', *entry))
    data[5] = struct.pack('<QQq', 0x158, (2 << 32) | 257, 0) + struct.pack('<QQq', 0x300, (3 << 32) | 257, 0)
    data[7] = b''.join(symbols)
    data[8] = bytes(strings)
    shstrings = bytearray(b'\0')
    nameoffs = [0]
    for name in names[1:]:
        nameoffs.append(len(shstrings))
        shstrings.extend(name.encode() + b'\0')
    data[9] = bytes(shstrings)
    types = [0, 1, 1, 1, 1, 4, 1, 2, 3, 3]
    flags = [0, 6, 2, 2, 3, 0, 2, 0, 0, 0]
    aligns = [0, 4, 4, 1, 64, 8, 8, 8, 1, 1]
    out = bytearray(64)
    offsets = [0]
    for i in range(1, len(names)):
        out.extend(bytes((-len(out)) % aligns[i]))
        offsets.append(len(out))
        out.extend(data[i])
    out.extend(bytes((-len(out)) % 8))
    shoff = len(out)
    for i in range(len(names)):
        link = 7 if i == 5 else 8 if i == 7 else 0
        info = 4 if i == 5 else 2 if i == 7 else 0
        entsize = 24 if i in (5, 7) else 0
        out.extend(struct.pack('<IIQQQQIIQQ', nameoffs[i], types[i], flags[i], 0, offsets[i],
                               len(data[i]), link, info, aligns[i], entsize))
    out[:16] = b'\x7fELF\x02\x01\x01' + bytes(9)
    struct.pack_into('<HHIQQQIHHHHHH', out, 16, 1, 183, 1, 0, 0, shoff, 0, 64, 0, 0, 64, 10, 9)
    return bytes(out)


REF = reference_fixture()
VERSIONS = ('\n'.join('0x%08x %s %s EXPORT_SYMBOL' %
            (0x12345678 if n == 'module_layout' else 1, n,
             'dv_compat_shim' if n in SHIM_EXPORTS else 'vmlinux')
             for n in sorted(KERNEL_EXPORTS | SHIM_EXPORTS)) + '\n').encode()


class ELFValidation(unittest.TestCase):
    def test_reference_layout(self):
        _, _, size, off, length, entries = p.target_reference(REF)
        self.assertEqual((size, off, length), (832, 24, 56))
        self.assertEqual(entries, {'init_module': 344, 'cleanup_module': 768})

    def test_malformed_headers_and_tables(self):
        elf = p.ELF(REF)
        shoff = struct.unpack_from('<Q', REF, 40)[0]
        mutations = []
        for off, value in [(4, 1), (5, 2), (6, 0), (16, 2), (18, 62), (52, 63), (58, 65)]:
            b = bytearray(REF); b[off] = value; mutations.append(b)
        b = bytearray(REF); struct.pack_into('<Q', b, shoff + 64 + 24, 1); mutations.append(b)
        b = bytearray(REF); struct.pack_into('<Q', b, shoff + 7 * 64 + 56, 23); mutations.append(b)
        b = bytearray(REF); struct.pack_into('<I', b, shoff + 5 * 64 + 40, 1); mutations.append(b)
        b = bytearray(REF); struct.pack_into('<Q', b, elf.byname['.rela.gnu.linkonce.this_module']['off'], 831); mutations.append(b)
        b = bytearray(REF); struct.pack_into('<Q', b, shoff + 2 * 64 + 24, elf.byname['.text']['off']); mutations.append(b)
        for mutated in mutations:
            with self.subTest(mutation=digest_short(mutated)):
                with self.assertRaises(p.Rejected): p.ELF(mutated)

    def test_module_info_repeated_descriptions_and_strict_singletons(self):
        suffix = (b'parmtype=dvshim_mp_calls:ulong\0parmtype=dv_shim_debug:uint\0'
                  b'parm=dvshim_mp_calls:parser calls\0parm=dv_shim_debug:debug flags\0'
                  b'alias=first\0alias=second\0firmware=one.bin\0firmware=two.bin\0'
                  b'author=one\0author=two\0description=first\0description=second\0')
        elf = p.ELF(REF); original = elf.byname['.modinfo']['data']
        elf.byname['.modinfo']['data'] = original + suffix
        info = elf.module_info()
        self.assertEqual(info[b'parmtype'], [b'dvshim_mp_calls:ulong', b'dv_shim_debug:uint'])
        self.assertEqual(info[b'parm'], [b'dvshim_mp_calls:parser calls', b'dv_shim_debug:debug flags'])
        self.assertEqual(info[b'alias'], [b'first', b'second'])
        self.assertEqual(p.target_reference(elf.encode())[2:5], (832, 24, 56))
        for record in (b'vermagic=duplicate\0', b'name=dv_compat_shim\0',
                       b'depends=one\0depends=two\0', b'license=GPL\0license=GPL\0',
                       b'unknown=one\0unknown=two\0', b'no_separator\0', b'=value\0',
                       b'bad key=value\0', b'\xff=value\0'):
            with self.subTest(record=record):
                invalid = p.ELF(REF); invalid.byname['.modinfo']['data'] = original + record
                with self.assertRaises(p.Rejected): invalid.module_info()
        elf.byname['.modinfo']['data'] = original.rstrip(b'\0')
        with self.assertRaises(p.Rejected): elf.module_info()

    def test_bad_reference_semantics(self):
        elf = p.ELF(REF)
        for mutate in ('duplicate_init', 'bad_addend', 'wrong_reloc', 'wrong_name', 'bad_constant'):
            b = bytearray(REF)
            at = elf.byname['.rela.gnu.linkonce.this_module']['off']
            if mutate == 'duplicate_init': struct.pack_into('<Q', b, at + 24 + 8, (2 << 32) | 257)
            if mutate == 'bad_addend': struct.pack_into('<q', b, at + 16, 1)
            if mutate == 'wrong_reloc': struct.pack_into('<Q', b, at + 8, (2 << 32) | 261)
            if mutate == 'wrong_name': b[elf.byname['.gnu.linkonce.this_module']['off'] + 24] = 88
            if mutate == 'bad_constant': struct.pack_into('<I', b, elf.byname['.rodata']['off'], 25)
            with self.subTest(mutation=mutate):
                with self.assertRaises(p.Rejected): p.target_reference(b)


def digest_short(data):
    return p.digest(data)[:8]


@unittest.skipUnless(BLOB is not None, 'optional module fixture: set DOVI5_TEST_SOURCE or --fixture')
class ExactBlob(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = BLOB.read_bytes()
        cls.result = p.canonical(cls.source, REF, VERSIONS, KERNEL_EXPORTS, PROFILE)

    def test_deterministic_crc_and_full_native_struct(self):
        self.assertEqual(self.result, p.canonical(self.source, REF, VERSIONS, KERNEL_EXPORTS, PROFILE))
        out = p.ELF(self.result)
        tm = out.byname['.gnu.linkonce.this_module']['data']
        expected = bytearray(832); expected[24:29] = b'dovi5'
        self.assertEqual(tm, bytes(expected))
        self.assertEqual(out.versions()['module_layout'], 0x12345678)
        self.assertEqual(set(out.versions()), out.imports() | {'module_layout'})
        self.assertNotIn('register_dv_functions', out.imports())
        self.assertIn(p.GUARD, out.imports())
        for name in ('.bss', '.data', '.rodata', '.init.text', '.exit.text'):
            self.assertEqual(out.byname[name]['data'], p.ELF(self.source).byname[name]['data'])

    def test_canonical_preserves_repeated_descriptive_records_and_symbol_offsets(self):
        elf = p.ELF(self.source)
        modinfo = elf.byname['.modinfo']
        first = b'parmtype=first:ulong\0'; second = b'parmtype=second:uint\0'
        first_offset = len(modinfo['data']); second_offset = first_offset + len(first)
        modinfo['data'] += first + second + b'parm=first:first parameter\0parm=second:second parameter\0'
        symbols = [sym for sym in elf.symbols if sym['index'] == modinfo['index'] and sym['size']]
        # Reuse two native local modinfo objects to exercise per-occurrence remapping.
        for sym, offset, record in zip(symbols[:2], (first_offset, second_offset), (first, second)):
            sym['value'], sym['size'] = offset, len(record)
        source = elf.encode()
        # Only this synthetic reconstruction test admits its deliberately modified fixture.
        # Production admission retains the exact proprietary fingerprint unchanged.
        with patch.object(p, 'SOURCE_SHA', p.digest(source)):
            result = p.canonical(source, REF, VERSIONS, KERNEL_EXPORTS, PROFILE)
            self.assertEqual(result, p.canonical(source, REF, VERSIONS, KERNEL_EXPORTS, PROFILE))
        out = p.ELF(result); info = out.module_info()
        self.assertEqual(info[b'parmtype'], [b'first:ulong', b'second:uint'])
        self.assertEqual(info[b'parm'], [b'first:first parameter', b'second:second parameter'])
        self.assertEqual(info[b'name'], [b'dovi5'])
        self.assertEqual(info[b'depends'], [b'dv_compat_shim'])
        for sym, expected in zip(symbols[:2], (first, second)):
            _, rewritten = out.symbol(sym['name'])
            self.assertEqual(out.byname['.modinfo']['data'][rewritten['value']:rewritten['value'] + rewritten['size']], expected)

    def test_guard_instructions_relocations_preserve_comparisons(self):
        original, out = p.ELF(self.source), p.ELF(self.result)
        guard_index, _ = out.symbol(p.GUARD)
        relocations = out.relocations[out.byname['.text']['index']][1]
        actual = [r for r in relocations if r[1] == guard_index]
        self.assertEqual(len(actual), 200)
        expected_text = bytearray(original.byname['.text']['data'])
        for pair in PROFILE:
            a, b = pair['adrp_text_offset'], pair['ldr_text_offset']
            olda, oldb = pair['original_adrp_word'], pair['original_ldr_word']
            struct.pack_into('<I', expected_text, a, 0x90000000 | olda & 31)
            struct.pack_into('<I', expected_text, b, oldb & ~(4095 << 10))
            self.assertIn((a, guard_index, 275, 0), actual)
            self.assertIn((b, guard_index, 286, 0), actual)
        self.assertEqual(out.byname['.text']['data'], expected_text)

    def test_complete_canonical_validation_rejects_modified_payload(self):
        args = (self.source, self.result, REF, VERSIONS, KERNEL_EXPORTS, PROFILE)
        self.assertEqual(p.validate_prepared(*args), p.digest(self.result))
        changed = bytearray(self.result)
        changed[p.ELF(changed).byname['.data']['off'] + 16] ^= 1
        with self.assertRaises(p.Rejected):
            p.validate_prepared(self.source, changed, REF, VERSIONS, KERNEL_EXPORTS, PROFILE)
        bad_profile = copy.deepcopy(PROFILE); bad_profile[0]['ldr_text_offset'] = bad_profile[1]['ldr_text_offset']
        with self.assertRaises(p.Rejected):
            p.canonical(self.source, REF, VERSIONS, KERNEL_EXPORTS, bad_profile)

    def test_reject_changed_or_prepared_source_missing_exports_crc_owner(self):
        mutated = bytearray(self.source); mutated[128] ^= 1
        bad_calls = [(mutated, REF, VERSIONS, KERNEL_EXPORTS, PROFILE),
                     (self.result, REF, VERSIONS, KERNEL_EXPORTS, PROFILE),
                     (self.source, REF, VERSIONS, KERNEL_EXPORTS - {'memcpy'}, PROFILE),
                     (self.source, REF, VERSIONS.replace(b'0x12345678', b'0x12345679'), KERNEL_EXPORTS, PROFILE),
                     (self.source, REF, VERSIONS.replace(b' dv_compat_shim ', b' wrong_owner '), KERNEL_EXPORTS, PROFILE),
                     (self.source, REF, VERSIONS.replace(b' memcpy vmlinux EXPORT_SYMBOL', b' memcpy vmlinux EXPORT_SYMBOL_GPL'), KERNEL_EXPORTS, PROFILE)]
        for args in bad_calls:
            with self.assertRaises(p.Rejected): p.canonical(*args)

    def test_preparation_optout_cache_missing_source_and_kernel_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('run', 'etc', 'storage/.config', 'flash', 'proc', 'usr/lib/coreelec'):
                (root / name).mkdir(parents=True, exist_ok=True)
            source = root / 'storage/.config/dovi5.ko'; source.write_bytes(self.source)
            ref = root / 'ref.ko'; ref.write_bytes(REF)
            versions = root / 'versions'; versions.write_bytes(VERSIONS)
            ksyms = root / 'proc/kallsyms'
            ksyms.write_text('\n'.join('0000000000000000 r __ksymtab_' + n for n in KERNEL_EXPORTS) + '\n')
            marker = root / 'run/dovi5-path'
            call = lambda release='4.9.269': prep.prepare(root, ref, versions, ksyms, release)
            call(); self.assertTrue(marker.is_file())
            active = Path(marker.read_text().strip())
            before = active.read_bytes()
            call(); self.assertEqual(before, active.read_bytes())
            self.assertEqual(prep.validate_load(root, ref, versions, ksyms, '4.9.269'), active)
            conf = root / 'storage/.config/dovi5.conf'; conf.write_text('ENABLE=no\n')
            self.assertIn('disabled', call()); self.assertFalse(marker.exists())
            self.assertEqual(source.read_bytes(), self.source)
            conf.write_text('ENABLE=invalid\n'); marker.write_text('stale\n')
            with self.assertRaises(p.Rejected): call()
            self.assertFalse(marker.exists())
            conf.unlink(); marker.write_text('stale\n')
            with self.assertRaises(p.Rejected): call('wrong-release')
            self.assertFalse(marker.exists())
            source.unlink(); self.assertIn('no compatible', call()); self.assertFalse(marker.exists())
            self.assertEqual(before, active.read_bytes())

    def test_preparation_state_symlink_rejects_without_touching_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('run', 'etc', 'storage/.config', 'flash', 'outside'):
                (root / name).mkdir(parents=True, exist_ok=True)
            source = root / 'storage/.config/dovi5.ko'; source.write_bytes(self.source)
            marker = root / 'run/dovi5-path'; marker.write_text('stale\n')
            (root / 'storage/.dovi5').symlink_to(root / 'outside', target_is_directory=True)
            with self.assertRaises(p.Rejected): prep.prepare(root)
            self.assertFalse(marker.exists())
            self.assertEqual(source.read_bytes(), self.source)
            self.assertEqual(list((root / 'outside').iterdir()), [])

    def generation_fixture(self, root):
        for name in ('run', 'etc', 'storage/.config', 'flash', 'proc'):
            (root / name).mkdir(parents=True, exist_ok=True)
        source = root / 'storage/.config/dovi5.ko'; source.write_bytes(self.source)
        ref = root / 'ref.ko'; ref.write_bytes(REF)
        versions = root / 'versions'; versions.write_bytes(VERSIONS)
        ksyms = root / 'proc/kallsyms'
        ksyms.write_text('\n'.join('0000000000000000 r __ksymtab_' + n for n in KERNEL_EXPORTS) + '\n')
        return (root, ref, versions, ksyms, '4.9.269')

    def overlay_reference_fixture(self, root, release='4.9.269'):
        args = list(self.generation_fixture(root))
        suffix = Path(release + '/kernel/drivers/amlogic/media/enhancement/amdolby_vision/dv_compat_shim.ko')
        target = root / 'usr/lib/kernel-overlays/base/lib/modules' / suffix
        target.parent.mkdir(parents=True)
        args[1].rename(target)
        runtime = root / 'run/kernel-overlays/modules' / suffix
        runtime.parent.mkdir(parents=True)
        runtime.symlink_to(target)
        (root / 'usr/lib/modules').symlink_to(root / 'run/kernel-overlays/modules', target_is_directory=True)
        (root / 'lib').symlink_to('usr/lib', target_is_directory=True)
        installed = root / 'lib/modules' / suffix
        # Stub only modinfo discovery. The real subprocess, symlink traversal,
        # regular-file reader, canonical patching and load validation all run.
        bin_dir = root / 'bin'; bin_dir.mkdir()
        modinfo = bin_dir / 'modinfo'
        modinfo.write_text('#!/bin/sh\n[ "$1" = -n ] && [ "$2" = dv_compat_shim ] || exit 1\nprintf "%s\\n" "$SHIM_TEST_REFERENCE"\n')
        modinfo.chmod(0o755)
        args[1] = None
        args[4] = release
        environment = dict(PATH=str(bin_dir) + os.pathsep + os.environ['PATH'], SHIM_TEST_REFERENCE=str(installed))
        return tuple(args), installed, target, environment

    def test_installed_reference_overlay_prepare_load_and_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); args, installed, target, environment = self.overlay_reference_fixture(root)
            with patch.dict(os.environ, environment):
                prep.prepare(*args)
                output = prep.validate_load(*args)
            self.assertEqual(output.read_bytes(), self.result)
            self.assertEqual(target.read_bytes(), REF)
            self.assertEqual((root / 'storage/.config/dovi5.ko').read_bytes(), self.source)
            standalone = root / 'standalone.ko'
            command = [sys.executable, str(SCRIPTS / 'dv5_patch.py')]
            options = [str(root / 'storage/.config/dovi5.ko'), str(standalone), '--ref', str(installed),
                       '--versions', str(args[2]), '--ksyms', str(args[3])]
            subprocess.run(command + ['patch'] + options, check=True, capture_output=True)
            subprocess.run(command + ['validate'] + options, check=True, capture_output=True)
            self.assertEqual(standalone.read_bytes(), self.result)
            # A linked installed reference never makes generated outputs link-safe.
            standalone.unlink(); standalone.symlink_to(output)
            rejected = subprocess.run(command + ['validate'] + options, capture_output=True)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertEqual(output.read_bytes(), self.result)

    def test_installed_reference_failures_retain_legacy_and_source(self):
        for kind in ('broken', 'loop', 'directory', 'fifo', 'identity', 'release', 'malformed'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); args, installed, target, environment = self.overlay_reference_fixture(root)
                with patch.dict(os.environ, environment):
                    prep.prepare(*args)
                    marker = root / 'run/dovi5-path'; output = Path(marker.read_text().strip())
                    before = output.read_bytes()
                    if kind == 'broken': target.unlink()
                    if kind == 'loop': target.unlink(); target.symlink_to(installed)
                    if kind == 'directory': target.unlink(); target.mkdir()
                    if kind == 'fifo': target.unlink(); os.mkfifo(target)
                    if kind in ('identity', 'release'):
                        elf = p.ELF(REF)
                        data = elf.byname['.modinfo']['data']
                        data = data.replace(b'name=dv_compat_shim', b'name=wrong_shim') if kind == 'identity' else data.replace(b'4.9.269', b'4.9.270')
                        elf.byname['.modinfo']['data'] = data; target.write_bytes(elf.encode())
                    if kind == 'malformed': target.write_bytes(b'not an ELF')
                    with self.assertRaises(p.Rejected): prep.validate_load(*args)
                    self.assertFalse(marker.exists())
                    marker.write_text('stale\n')
                    with self.assertRaises(p.Rejected): prep.prepare(*args)
                    self.assertFalse(marker.exists())
                    self.assertEqual(output.read_bytes(), before)
                    self.assertEqual((root / 'storage/.config/dovi5.ko').read_bytes(), self.source)

    def test_interrupted_generation_preserves_complete_previous_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); args = self.generation_fixture(root)
            prep.prepare(*args)
            old = Path((root / 'run/dovi5-path').read_text().strip())
            old_stamp = old.with_name('state.json').read_bytes()
            # New target import CRC causes a new canonical generation; failure before commit retains prior pair.
            args[2].write_bytes(VERSIONS.replace(b'0x00000001 memcpy', b'0x00000002 memcpy'))
            real_write = prep.atomic_write
            def interrupted(path, data, source=None):
                if Path(path).name == 'state.json': raise OSError('simulated full storage')
                return real_write(path, data, source)
            with patch('dovi5_prepare.atomic_write', side_effect=interrupted):
                with self.assertRaises(OSError): prep.prepare(*args)
            self.assertEqual(old.read_bytes(), self.result)
            self.assertEqual(old.with_name('state.json').read_bytes(), old_stamp)
            self.assertFalse((root / 'run/dovi5-path').exists())
            self.assertEqual(list(old.parent.parent.glob('.prepare-*')), [])
            self.assertEqual((root / 'storage/.config/dovi5.ko').read_bytes(), self.source)

    def test_load_validation_rejects_manifest_output_live_exports_and_links(self):
        for kind in ('stamp', 'payload', 'live_exports', 'symlink', 'hardlink', 'marker',
                     'stamp_symlink', 'stamp_hardlink', 'marker_symlink', 'generation_symlink'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); args = self.generation_fixture(root); prep.prepare(*args)
                marker = root / 'run/dovi5-path'; output = Path(marker.read_text().strip())
                if kind == 'stamp': output.with_name('state.json').write_text('{}\n')
                if kind == 'payload': output.write_bytes(b'incompatible')
                if kind == 'live_exports': args[3].write_text('0000000000000000 r __ksymtab_module_layout\n')
                if kind == 'symlink':
                    original = root / 'storage/.config/dovi5.ko'; output.unlink(); output.symlink_to(original)
                if kind == 'hardlink':
                    original = root / 'storage/.config/dovi5.ko'; output.unlink(); os.link(original, output)
                if kind == 'marker': marker.write_text('/storage/.config/dovi5.ko\n')
                if kind in ('stamp_symlink', 'stamp_hardlink', 'marker_symlink'):
                    linked = marker if kind == 'marker_symlink' else output.with_name('state.json')
                    preserved = root / 'preserved'; preserved.write_bytes(linked.read_bytes())
                    linked.unlink()
                    if kind == 'stamp_hardlink': os.link(preserved, linked)
                    else: linked.symlink_to(preserved)
                if kind == 'generation_symlink':
                    generation = output.parent
                    preserved_dir = root / 'preserved-generation'
                    generation.rename(preserved_dir); generation.symlink_to(preserved_dir, target_is_directory=True)
                with self.assertRaises((p.Rejected, OSError)): prep.validate_load(*args)
                self.assertFalse(marker.exists())
                self.assertEqual((root / 'storage/.config/dovi5.ko').read_bytes(), self.source)


@unittest.skipUnless(REFERENCES, 'optional actual-reference check: pass --reference, --versions and --exports')
class ActualReference(unittest.TestCase):
    def test_complete_preparation_load_validation_and_standalone_with_actual_artifacts(self):
        source = BLOB.read_bytes()
        versions = fixture_args.versions.read_bytes()
        export_bytes = fixture_args.exports.read_bytes()
        exports = p.kernel_exports(fixture_args.exports)
        records = []
        outputs = []
        for ref_path in REFERENCES:
            with self.subTest(reference=str(ref_path)), tempfile.TemporaryDirectory() as tmp:
                ref_data = p.read_module_reference(ref_path)
                ref, vermagic, size, offset, length, entries = p.target_reference(ref_data)
                release = vermagic.split()[0].decode('ascii')
                expected = p.canonical(source, ref_data, versions, exports, PROFILE)
                out = p.ELF(expected)
                self.assertEqual(out.module_info()[b'vermagic'], [vermagic])
                self.assertEqual(out.module_info()[b'name'], [b'dovi5'])
                self.assertEqual(out.versions()['module_layout'], ref.versions()['module_layout'])
                root = Path(tmp)
                fixture = ExactBlob(); fixture.source = source
                args, installed, target, environment = fixture.overlay_reference_fixture(root, release)
                target.write_bytes(ref_data); args[2].write_bytes(versions); args[3].write_bytes(export_bytes)
                env = dict(os.environ, **environment)
                options = ['--root', str(root), '--versions', str(args[2]), '--ksyms', str(args[3]), '--kernel-release', release]
                prepare_command = [sys.executable, str(SCRIPTS / 'dovi5_prepare.py')]
                subprocess.run(prepare_command + ['prepare'] + options, env=env, check=True, capture_output=True)
                result = subprocess.run(prepare_command + ['validate-load'] + options, env=env, check=True, capture_output=True, text=True)
                output = Path(result.stdout.strip())
                self.assertEqual(output.read_bytes(), expected)
                self.assertEqual((root / 'run/dovi5-path').read_text(), str(output) + '\n')
                with patch.dict(os.environ, environment):
                    self.assertEqual(prep.validate_load(*args), output)
                standalone = root / 'standalone.ko'
                patch_command = [sys.executable, str(SCRIPTS / 'dv5_patch.py')]
                patch_options = [str(root / 'storage/.config/dovi5.ko'), str(standalone), '--ref', str(installed),
                                 '--versions', str(args[2]), '--ksyms', str(args[3])]
                subprocess.run(patch_command + ['patch'] + patch_options, check=True, capture_output=True)
                subprocess.run(patch_command + ['validate'] + patch_options, check=True, capture_output=True)
                self.assertEqual(standalone.read_bytes(), expected)
                # Exact canonical bytes via a symlink must still fail prepared-output admission.
                preserved = root / 'preserved.ko'; output.rename(preserved); output.symlink_to(preserved)
                invalid = subprocess.run(prepare_command + ['validate-load'] + options, env=env, capture_output=True)
                self.assertNotEqual(invalid.returncode, 0)
                self.assertFalse((root / 'run/dovi5-path').exists())
                self.assertEqual(preserved.read_bytes(), expected)
                self.assertEqual(target.read_bytes(), ref_data)
                self.assertEqual((root / 'storage/.config/dovi5.ko').read_bytes(), source)
                self.assertEqual(p.read_module_reference(ref_path), ref_data)
                outputs.append(p.digest(expected))
                records.append(dict(reference=str(ref_path), reference_sha256=p.digest(ref_data), reference_size=len(ref_data),
                                    versions=str(fixture_args.versions), versions_sha256=p.digest(versions),
                                    exports_source=str(fixture_args.exports), exports_sha256=p.digest(export_bytes),
                                    source_sha256=p.digest(source), profile_sha256=p.digest((SCRIPTS / 'canary-profile.json').read_bytes()),
                                    native_module_size=size, name_offset=offset, name_size=length, relocations=entries,
                                    metadata_counts={k.decode('ascii'): len(v) for k, v in ref.module_info().items()},
                                    output_size=len(expected), output_sha256=p.digest(expected), prepare=True, validate_load=True,
                                    standalone=True, prepared_symlink_rejected=True, device_loaded=False))
        self.assertEqual(len(set(outputs)), 1, 'equivalent native references must produce identical canonical output')
        if fixture_args.reference_report:
            fixture_args.reference_report.write_text(json.dumps(records, indent=2) + '\n')


class Loader(unittest.TestCase):
    @unittest.skipUnless(LOADER is not None, 'repository loader unavailable; pass --loader for isolated fixture')
    def test_ne_unchanged_and_original_required_with_fallbacks(self):
        loader = LOADER.read_text()
        # Pin the ne code and version policy, now including successful-load provenance.
        self.assertEqual(hashlib.sha256(loader[:loader.index('original_dovi_loaded()')].encode()).hexdigest(),
                         '6c83d056b91000c40e2cd79df0ac412bd329f2e70017bcc43f4219184fafe2f3')
        self.assertIn('DOVI5_KO=$(/usr/lib/coreelec/dovi5-prepare validate-load)', loader)
        body = loader[loader.index('original_dovi_loaded()'):loader.index('\nmessage "run dovi')]
        scenarios = [('missing', False, False, False, False),
                     ('original_failed', True, False, True, False),
                     ('both', True, True, True, True),
                     ('new_rejected', True, True, False, False),
                     ('shim_failed', True, True, True, False),
                     ('new_load_failed', True, True, True, True),
                     ('vendor_preexisting', False, True, True, True),
                     ('vendor_owned', False, True, True, True)]
        for name, oldfile, oldsuccess, validation, newattempt in scenarios:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                for path in ('storage/.config', 'flash', 'android/vendor/lib/modules', 'run'):
                    (root / path).mkdir(parents=True)
                (root / 'run/dovi-loaded-path').write_text('stale-original\n')
                (root / 'run/dovi5-loaded-path').write_text('stale-new\n')
                if oldfile: (root / 'storage/.config/dovi.ko').write_bytes(b'original')
                if name.startswith('vendor_'): (root / 'android/vendor/lib/modules/dovi.ko').write_bytes(b'original')
                adapted = body.replace('/storage/', str(root / 'storage') + '/').replace('/flash/', str(root / 'flash') + '/')
                adapted = adapted.replace('/android/', str(root / 'android') + '/')
                adapted = adapted.replace('/run/', str(root / 'run') + '/')
                adapted = adapted.replace('/sys/module/', str(root / 'sys/module') + '/')
                adapted = adapted.replace('/usr/lib/coreelec/dovi5-prepare validate-load', 'mock_validate_load')
                adapted = adapted.replace('[ -b /dev/vendor ]', '[ "$MOCK_VENDOR_BLOCK" = yes ]')
                mock = '''
message() { echo "$*" >&2; }
original_dovi_loaded() { return 1; }
mountpoint() { [ "$MOCK_VENDOR_MOUNTED" = yes ]; }
mount() { echo mount >> "$LOG"; [ "$MOCK_VENDOR_BLOCK" = yes ]; }
umount() { echo umount >> "$LOG"; }
insmod() {
  echo "insmod $1" >> "$LOG"
  case "$1" in */generations/*) [ "$NEW_SUCCESS" = yes ];; *) [ "$OLD_SUCCESS" = yes ];; esac
}
modprobe() { echo shim >> "$LOG"; [ "$SHIM_SUCCESS" = yes ]; }
mock_validate_load() {
  echo validate >> "$LOG"
  [ "$VALID" = yes ] || return 1
  echo /storage/.dovi5/generations/example/dovi5.ko
}
load_dovi_ng
'''
                env = dict(os.environ, LOG=str(root / 'log'), OLD_SUCCESS='yes' if oldsuccess else 'no',
                           VALID='yes' if validation else 'no', SHIM_SUCCESS='no' if name == 'shim_failed' else 'yes',
                           NEW_SUCCESS='no' if name == 'new_load_failed' else 'yes',
                           MOCK_VENDOR_BLOCK='yes' if name == 'vendor_owned' else 'no',
                           MOCK_VENDOR_MOUNTED='yes' if name == 'vendor_preexisting' else 'no')
                result = subprocess.run(['bash', '-c', adapted + mock], env=env, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = (root / 'log').read_text() if (root / 'log').exists() else ''
                self.assertEqual('/generations/' in calls, newattempt)
                if not oldsuccess:
                    self.assertNotIn('shim', calls); self.assertNotIn('validate', calls)
                self.assertNotIn('unexpected_', calls)
                self.assertEqual('umount\n' in calls, name == 'vendor_owned')
                old_record, new_record = root / 'run/dovi-loaded-path', root / 'run/dovi5-loaded-path'
                self.assertEqual(old_record.exists(), oldsuccess)
                self.assertEqual(new_record.exists(), newattempt and name != 'new_load_failed')
                if oldsuccess:
                    expected = root / ('android/vendor/lib/modules/dovi.ko' if name.startswith('vendor_') else 'storage/.config/dovi.ko')
                    self.assertEqual(old_record.read_text(), str(expected) + '\n')
                if new_record.exists():
                    self.assertEqual(new_record.read_text(), '/storage/.dovi5/generations/example/dovi5.ko\n')
                self.assertFalse(list((root / 'run').glob('*.tmp')))



    def test_cleanup_retains_failed_unload_and_ne_records_success(self):
        loader = LOADER.read_text()
        body = loader[loader.index('original_dovi_loaded()'):loader.index('\nmessage "run dovi')]
        ne = loader[loader.index('insmod_dovi_ne()'):loader.index('load_dovi_ne()')]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'run').mkdir()
            for module in ('dovi', 'dovi5'):
                (root / ('sys/module/' + module)).mkdir(parents=True)
                (root / ('run/' + module + '-loaded-path')).write_text('/known/' + module + '.ko\n')
            adapted = body.replace('/run/', str(root / 'run') + '/').replace('/sys/module/', str(root / 'sys/module') + '/')
            # Preloaded modules have no invented path; failed unload retains
            # existing provenance, successful unload clears it.
            subprocess.run(['bash', '-c', adapted + '\nrmmod() { return 1; }\ncleanup_dovi_ng'], check=True)
            self.assertTrue((root / 'run/dovi-loaded-path').exists())
            self.assertTrue((root / 'run/dovi5-loaded-path').exists())
            for module in ('dovi', 'dovi5'):
                (root / ('sys/module/' + module)).rmdir()
            subprocess.run(['bash', '-c', adapted + '\nrmmod() { return 0; }\ncleanup_dovi_ng'], check=True)
            self.assertFalse((root / 'run/dovi-loaded-path').exists())
            self.assertFalse((root / 'run/dovi5-loaded-path').exists())
            source = root / 'dovi.ko'; source.write_text('original')
            mock = '\nmessage() { :; }\nmodinfo() { :; }\ncheck_dovi_version() { return 0; }\n'
            success = adapted + ne + mock + '\ninsmod() { return 0; }\ninsmod_dovi_ne "' + str(source) + '"'
            subprocess.run(['bash', '-c', success], check=True)
            self.assertEqual((root / 'run/dovi-loaded-path').read_text(), str(source) + '\n')
            (root / 'run/dovi-loaded-path').unlink()
            failure = adapted + ne + mock + '\ninsmod() { return 1; }\ninsmod_dovi_ne "' + str(source) + '"'
            self.assertNotEqual(subprocess.run(['bash', '-c', failure]).returncode, 0)
            self.assertFalse((root / 'run/dovi-loaded-path').exists())


class FilesystemSafety(unittest.TestCase):
    def test_atomic_failure_links_identity_and_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'source'; source.write_bytes(b'original')
            output = root / 'out'; output.write_bytes(b'cached')
            with self.assertRaises(p.Rejected): p.atomic_write(source, b'changed', source)
            output.unlink(); output.symlink_to(source)
            with self.assertRaises(p.Rejected): p.atomic_write(output, b'changed', source)
            output.unlink(); os.link(source, output)
            with self.assertRaises(p.Rejected): p.atomic_write(output, b'changed', source)
            output.unlink(); output.write_bytes(b'cached')
            with patch('dv5_patch.os.replace', side_effect=OSError('interrupted')):
                with self.assertRaises(OSError): p.atomic_write(output, b'new', source)
            self.assertEqual(output.read_bytes(), b'cached')
            self.assertEqual(source.read_bytes(), b'original')
            self.assertFalse(list(root.glob('.dovi5-*')))
            p.atomic_write(output, b'new', source)
            self.assertEqual(output.read_bytes(), b'new')


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *unittest_args], verbosity=2)
