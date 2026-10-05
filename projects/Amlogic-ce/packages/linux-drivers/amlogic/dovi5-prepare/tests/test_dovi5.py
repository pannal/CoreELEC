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
fixture_parser.add_argument('--loader', type=Path, default=os.environ.get('DOVI5_TEST_LOADER'))
fixture_args, unittest_args = fixture_parser.parse_known_args()
BLOB = fixture_args.fixture
if BLOB is not None and not BLOB.is_file():
    raise SystemExit('Explicit Dolby module test fixture is not a regular file')
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
        for kind in ('stamp', 'payload', 'live_exports', 'symlink', 'hardlink', 'marker'):
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
                with self.assertRaises((p.Rejected, OSError)): prep.validate_load(*args)
                self.assertFalse(marker.exists())
                self.assertEqual((root / 'storage/.config/dovi5.ko').read_bytes(), self.source)


class Loader(unittest.TestCase):
    @unittest.skipUnless(LOADER is not None, 'repository loader unavailable; pass --loader for isolated fixture')
    def test_ne_unchanged_and_original_required_with_fallbacks(self):
        loader = LOADER.read_text()
        # Pin the existing ne code, version check and license prefix without workstation paths.
        self.assertEqual(hashlib.sha256(loader[:loader.index('original_dovi_loaded()')].encode()).hexdigest(),
                         '81ed84422797a0f5adebb6abea5546bd743a8805c6d83e7f644358589edc4b37')
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
                for path in ('storage/.config', 'flash', 'android/vendor/lib/modules'):
                    (root / path).mkdir(parents=True)
                if oldfile: (root / 'storage/.config/dovi.ko').write_bytes(b'original')
                if name.startswith('vendor_'): (root / 'android/vendor/lib/modules/dovi.ko').write_bytes(b'original')
                adapted = body.replace('/storage/', str(root / 'storage') + '/').replace('/flash/', str(root / 'flash') + '/')
                adapted = adapted.replace('/android/', str(root / 'android') + '/')
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
