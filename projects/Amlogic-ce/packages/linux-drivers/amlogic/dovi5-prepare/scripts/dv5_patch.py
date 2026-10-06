#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exact-source ELF preparation bound to native target references and symbol CRCs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import tempfile

SOURCE_SHA = 'f6c26659a255447685ceac9441e399c999b1fae9c6435c48d70e14a14dd7f8f7'
MAX_BYTES = 64 * 1024 * 1024
# These descriptive tags have one record per parameter, alias, firmware or author.
# All other fields, including identity, compatibility and license, stay singleton.
REPEATED_MODINFO = {b'parm', b'parmtype', b'alias', b'firmware', b'author', b'description'}
RENAME = {'register_dv_functions': 'register_dv5shim_func',
          'unregister_dv_functions': 'unregister_dv5shim_func'}
GUARD = 'dv5_stack_chk_guard'
RELOC_WIDTH = {257: 8, 261: 4, 275: 4, 277: 4, 278: 4,
               282: 4, 283: 4, 284: 4, 285: 4, 286: 4}
SHDR = struct.Struct('<IIQQQQIIQQ')
SYMBOL = struct.Struct('<IBBHQQ')
RELA = struct.Struct('<QQq')


class Rejected(ValueError):
    pass


def require(ok, reason):
    if not ok:
        raise Rejected(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_regular(path, allow_links=False):
    flags = os.O_RDONLY | os.O_NONBLOCK
    if not allow_links:
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        st = os.fstat(fd)
        require(stat.S_ISREG(st.st_mode), 'not a regular file: ' + str(path))
        require(0 < st.st_size <= MAX_BYTES, 'invalid file size: ' + str(path))
        if not allow_links:
            require(st.st_nlink == 1, 'hard-linked file: ' + str(path))
        with os.fdopen(os.dup(fd), 'rb') as stream:
            data = stream.read(MAX_BYTES + 1)
        require(len(data) == st.st_size, 'file changed during read: ' + str(path))
        return data
    finally:
        os.close(fd)


def read_module_reference(path):
    """Read an installed input module through normal kernel-overlay symlinks.

    Resolve strictly, then use the unchanged regular-file checks on the target.
    This is only for reference inputs; generated files must remain link-free.
    """
    try:
        resolved = Path(path).resolve(strict=True)
        return read_regular(resolved)
    except (OSError, RuntimeError, Rejected) as exc:
        raise Rejected('cannot read installed module reference ' + str(path) + ': ' + str(exc)) from exc


def string(table, offset):
    require(0 <= offset < len(table), 'string offset outside table')
    end = table.find(b'\0', offset)
    require(end >= 0, 'unterminated string')
    try:
        return table[offset:end].decode('ascii')
    except UnicodeDecodeError as exc:
        raise Rejected('non-ASCII ELF name') from exc


class ELF:
    def __init__(self, data):
        data = bytes(data)
        require(64 <= len(data) <= MAX_BYTES, 'invalid ELF size')
        require(data[:16] == b'\x7fELF\x02\x01\x01' + bytes(9), 'unsupported ELF identity')
        fields = struct.unpack_from('<HHIQQQIHHHHHH', data, 16)
        typ, machine, version, entry, phoff, shoff, flags, ehsize, phsize, phnum, shsize, shnum, shstr = fields
        require((typ, machine, version, entry, phoff, flags, ehsize, phsize, phnum, shsize) ==
                (1, 183, 1, 0, 0, 0, 64, 0, 0, 64), 'unsupported ELF header')
        require(0 < shnum < 65535 and 0 < shstr < shnum, 'unsupported extended section indexing')
        require(shoff >= 64 and shoff % 8 == 0 and shoff + shnum * 64 <= len(data), 'section table outside file')
        self.sections = []
        occupied = [(0, 64), (shoff, shoff + shnum * 64)]
        for index in range(shnum):
            name, typ, flags, address, off, size, link, info, align, entsize = SHDR.unpack_from(data, shoff + index * 64)
            require(address == 0, 'relocatable section has nonzero address')
            require(align == 0 or (align & (align - 1) == 0 and align <= 1 << 20), 'invalid section alignment')
            require(0 <= link < shnum, 'invalid section link')
            if typ != 8 and size:
                require(off >= 64 and off + size <= len(data), 'section outside file')
                require(not align or off % align == 0, 'unaligned section')
                occupied.append((off, off + size))
            payload = b'' if typ == 8 else data[off:off + size]
            self.sections.append(dict(index=index, name_off=name, type=typ, flags=flags, off=off,
                                      size=size, link=link, info=info, align=align, entsize=entsize,
                                      data=payload))
        require(self.sections[0] == dict(index=0, name_off=0, type=0, flags=0, off=0, size=0,
                                        link=0, info=0, align=0, entsize=0, data=b''), 'invalid null section')
        occupied.sort()
        require(all(a[1] <= b[0] for a, b in zip(occupied, occupied[1:])), 'overlapping ELF data')
        names = self.sections[shstr]
        require(names['type'] == 3 and names['data'][:1] == b'\0', 'invalid section name table')
        self.byname = {}
        for sec in self.sections:
            sec['name'] = string(names['data'], sec['name_off'])
            require(sec['name'] not in self.byname, 'duplicate section name')
            self.byname[sec['name']] = sec
        self.shstr = shstr
        self.symsec = self.section('.symtab', 2)
        require(self.symsec['entsize'] == 24 and self.symsec['size'] % 24 == 0, 'invalid symbol records')
        strs = self.sections[self.symsec['link']]
        require(strs['type'] == 3 and strs['data'][:1] == b'\0', 'invalid symbol string table')
        self.strsec = strs
        self.symbols = []
        for off in range(0, self.symsec['size'], 24):
            name, info, other, index, value, size = SYMBOL.unpack_from(self.symsec['data'], off)
            require(index < shnum or index == 0xfff1, 'invalid symbol section index')
            require(info >> 4 in (0, 1, 2) and other & ~3 == 0, 'unsupported symbol binding/visibility')
            if 0 < index < shnum:
                target = self.sections[index]
                require(value <= target['size'] and size <= target['size'] - value,
                        'symbol outside section: ' + string(strs['data'], name) + ' in ' + target['name'])
            self.symbols.append(dict(name=string(strs['data'], name), info=info, other=other,
                                     index=index, value=value, size=size))
        require(self.symbols[0] == dict(name='', info=0, other=0, index=0, value=0, size=0), 'invalid null symbol')
        require(0 < self.symsec['info'] <= len(self.symbols), 'invalid local symbol boundary')
        require(all((s['info'] >> 4 == 0) == (i < self.symsec['info'])
                    for i, s in enumerate(self.symbols)), 'inconsistent local symbol ordering')
        self.relocations = {}
        for sec in self.sections:
            require(sec['type'] != 9, 'REL relocations unsupported')
            if sec['type'] != 4:
                continue
            require(sec['entsize'] == 24 and sec['size'] % 24 == 0, 'invalid relocation records')
            require(sec['link'] == self.symsec['index'] and 0 < sec['info'] < shnum, 'invalid relocation linkage')
            target = self.sections[sec['info']]
            records = []
            ranges = []
            for off in range(0, sec['size'], 24):
                loc, info, addend = RELA.unpack_from(sec['data'], off)
                sym, typ = info >> 32, info & 0xffffffff
                require(sym < len(self.symbols) and typ in RELOC_WIDTH, 'unsupported relocation/symbol')
                width = RELOC_WIDTH[typ]
                require(loc % (4 if width == 4 else 8) == 0 and loc + width <= target['size'], 'relocation outside target')
                ranges.append((loc, loc + width))
                records.append((loc, sym, typ, addend))
            ranges.sort()
            require(all(a[1] <= b[0] for a, b in zip(ranges, ranges[1:])), 'overlapping relocation records')
            require(sec['info'] not in self.relocations, 'multiple relocation sections for target')
            self.relocations[sec['info']] = (sec, records)

    def section(self, name, typ=None):
        require(name in self.byname, 'missing section: ' + name)
        sec = self.byname[name]
        require(typ is None or sec['type'] == typ, 'wrong section type: ' + name)
        return sec

    def symbol(self, name):
        result = [(i, s) for i, s in enumerate(self.symbols) if s['name'] == name]
        require(len(result) == 1, 'missing or duplicate symbol: ' + name)
        return result[0]

    def imports(self):
        return {s['name'] for s in self.symbols if s['index'] == 0 and s['name']}

    def exports(self):
        return {s['name'][10:] for s in self.symbols
                if s['index'] != 0 and s['name'].startswith('__ksymtab_')}

    def module_info(self):
        data = self.section('.modinfo', 1)['data']
        require(self.byname['.modinfo']['flags'] & 2 and data.endswith(b'\0'), 'invalid module info')
        result = {}
        for item in data.split(b'\0'):
            if not item:
                continue
            key, sep, value = item.partition(b'=')
            require(sep and re.fullmatch(rb'[A-Za-z_][A-Za-z0-9_]*', key), 'invalid module info key')
            require(key not in result or key in REPEATED_MODINFO, 'duplicate singleton module info key: ' + key.decode('ascii'))
            result.setdefault(key, []).append(value)
        return result

    def versions(self):
        sec = self.section('__versions', 1)
        require(sec['flags'] & 2 and sec['size'] % 64 == 0, 'invalid symbol version section')
        result = {}
        for off in range(0, sec['size'], 64):
            crc = struct.unpack_from('<Q', sec['data'], off)[0]
            name = string(sec['data'][off + 8:off + 64], 0)
            require(name and name not in result and crc <= 0xffffffff, 'invalid/duplicate symbol version')
            result[name] = crc
        return result

    def constant(self, name):
        _, sym = self.symbol(name)
        require(sym['index'] > 0 and sym['size'] in (4, 8), 'invalid reference layout constant')
        sec = self.sections[sym['index']]
        require(sec['type'] == 1 and sec['flags'] & 2 and not sec['flags'] & 1, 'mutable reference layout constant')
        return int.from_bytes(sec['data'][sym['value']:sym['value'] + sym['size']], 'little')

    def encode(self):
        # Rebuild every file offset and name index. Preserve section indices and symbol ordering.
        symstrings = bytearray(b'\0')
        symdata = bytearray()
        for sym in self.symbols:
            off = 0
            if sym['name']:
                off = len(symstrings)
                symstrings.extend(sym['name'].encode('ascii') + b'\0')
            symdata.extend(SYMBOL.pack(off, sym['info'], sym['other'], sym['index'], sym['value'], sym['size']))
        self.strsec['data'] = bytes(symstrings)
        self.symsec['data'] = bytes(symdata)
        names = bytearray(b'\0')
        for sec in self.sections[1:]:
            sec['name_off'] = len(names)
            names.extend(sec['name'].encode('ascii') + b'\0')
        self.sections[self.shstr]['data'] = bytes(names)
        out = bytearray(64)
        for sec in self.sections[1:]:
            align = max(1, sec['align'])
            out.extend(bytes((-len(out)) % align))
            sec['off'] = len(out)
            if sec['type'] != 8:
                sec['size'] = len(sec['data'])
                out.extend(sec['data'])
        out.extend(bytes((-len(out)) % 8))
        shoff = len(out)
        for sec in self.sections:
            out.extend(SHDR.pack(sec['name_off'], sec['type'], sec['flags'], 0, sec['off'],
                                sec['size'], sec['link'], sec['info'], sec['align'], sec['entsize']))
        out[:16] = b'\x7fELF\x02\x01\x01' + bytes(9)
        struct.pack_into('<HHIQQQIHHHHHH', out, 16, 1, 183, 1, 0, 0, shoff, 0, 64, 0, 0, 64,
                         len(self.sections), self.shstr)
        return bytes(out)


def target_reference(data):
    elf = ELF(data)
    info = elf.module_info()
    vermagic = info.get(b'vermagic', [b''])[0]
    require(vermagic.endswith(b' modversions aarch64') and b' mod_unload ' in vermagic,
            'reference lacks required version/architecture/unload checks')
    require(info.get(b'name', [b'dv_compat_shim'])[0] == b'dv_compat_shim', 'wrong reference module')
    tm = elf.section('.gnu.linkonce.this_module', 1)
    require(tm['flags'] & 3 == 3 and 0 < tm['size'] <= 4096, 'invalid native module layout')
    _, symbol = elf.symbol('__this_module')
    require(symbol['index'] == tm['index'] and symbol['value'] == 0 and symbol['size'] == tm['size'], 'native module size mismatch')
    offset = elf.constant('dv5_module_name_offset')
    length = elf.constant('dv5_module_name_size')
    require(offset == 24 and length >= len(b'dv_compat_shim\0') and offset + length <= tm['size'], 'invalid native module name slot')
    require(tm['data'][offset:offset + length].split(b'\0')[0] == b'dv_compat_shim', 'native name offset mismatch')
    require(tm['index'] in elf.relocations, 'reference has no module relocations')
    _, records = elf.relocations[tm['index']]
    require(len(records) == 2, 'unexpected native module relocations')
    offsets = {}
    for loc, sym, typ, addend in records:
        name = elf.symbols[sym]['name']
        require(name in ('init_module', 'cleanup_module') and typ == 257 and addend == 0 and name not in offsets,
                'invalid native init/exit relocations')
        offsets[name] = loc
    require(len(offsets) == 2, 'native init/exit absent')
    require(all(loc + 8 <= offset or loc >= offset + length for loc in offsets.values()),
            'native init/exit overlaps module name slot')
    require(GUARD in elf.exports() and set(RENAME.values()) <= elf.exports(), 'required shim exports missing')
    return elf, vermagic, tm['size'], offset, length, offsets


def parse_symvers(data):
    result = {}
    for line in data.decode('ascii').splitlines():
        parts = line.split()
        require(len(parts) in (4, 5), 'malformed Module.symvers record')
        crc, name, module, export = parts[:4]
        require(name and name not in result and export in ('EXPORT_SYMBOL', 'EXPORT_SYMBOL_GPL',
                'EXPORT_SYMBOL_GPL_FUTURE', 'EXPORT_UNUSED_SYMBOL', 'EXPORT_UNUSED_SYMBOL_GPL'),
                'unsupported or duplicate export')
        value = int(crc, 16)
        require(0 <= value <= 0xffffffff, 'invalid CRC')
        result[name] = (value, module, export)
    require('module_layout' in result, 'Module.symvers lacks module_layout')
    return result


def kernel_exports(path):
    result = set()
    # /proc/kallsyms reports size0; bounded streaming read intentionally differs from regular module IO.
    with open(path, encoding='ascii') as stream:
        for line in stream:
            parts = line.split()
            require(len(parts) in (3, 4), 'malformed kallsyms line')
            require(len(parts[0]) == 16 and all(c in '0123456789abcdefABCDEF' for c in parts[0]), 'invalid kallsyms address')
            if parts[2].startswith('__ksymtab_') and len(parts) == 3:
                result.add(parts[2][10:])
    require(result, 'no readable built-in kernel exports')
    return result


def canonical(source, reference, symvers_data, exports, profile):
    require(digest(source) == SOURCE_SHA, 'unsupported proprietary source fingerprint')
    elf = ELF(source)
    ref, vermagic, size, name_off, name_size, offsets = target_reference(reference)
    versions = parse_symvers(symvers_data)
    ref_versions = ref.versions()
    require(ref_versions.get('module_layout') == versions['module_layout'][0], 'kernel/reference module_layout CRC mismatch')
    for name, crc in ref_versions.items():
        require(name in versions and versions[name][0] == crc, 'reference/symvers CRC mismatch: ' + name)
    imports = {RENAME.get(n, n) for n in elf.imports()} | {GUARD}
    shim_exports = ref.exports()
    require(imports <= exports | shim_exports, 'unresolved running target imports: ' + ' '.join(sorted(imports - exports - shim_exports)))
    for name in imports | {'module_layout'}:
        require(name in versions, 'missing target CRC: ' + name)
        module = versions[name][1]
        require(versions[name][2] == 'EXPORT_SYMBOL', 'proprietary module cannot use GPL export: ' + name)
        if name in shim_exports:
            require(Path(module).name in ('dv_compat_shim', 'dv_compat_shim.ko'), 'shim export owner mismatch: ' + name)
        else:
            require(module == 'vmlinux' and name in exports, 'kernel export owner mismatch: ' + name)
    info = elf.module_info()
    old_info_offsets = {}
    occurrences = {}
    position = 0
    for item in elf.byname['.modinfo']['data'].split(b'\0'):
        if item:
            key = item.partition(b'=')[0]
            occurrence = occurrences.get(key, 0)
            old_info_offsets[position] = key, occurrence
            occurrences[key] = occurrence + 1
        position += len(item) + 1
    info.update({b'vermagic': [vermagic], b'name': [b'dovi5'], b'depends': [b'dv_compat_shim']})
    info_data = bytearray()
    new_info_offsets = {}
    for key, values in sorted(info.items()):
        for occurrence, value in enumerate(values):
            entry = key + b'=' + value + b'\0'
            new_info_offsets[key, occurrence] = (len(info_data), len(entry))
            info_data.extend(entry)
    for sym in elf.symbols:
        if sym['index'] != elf.byname['.modinfo']['index'] or not sym['size']:
            continue
        require(sym['value'] in old_info_offsets, 'unrecognized module info object')
        record = old_info_offsets[sym['value']]
        sym['value'], sym['size'] = new_info_offsets[record]
    elf.byname['.modinfo']['data'] = bytes(info_data)
    tm = elf.section('.gnu.linkonce.this_module', 1)
    _, records = elf.relocations[tm['index']]
    require(len(records) == 2, 'unexpected source module relocation count')
    target_data = bytearray(size)
    target_data[name_off:name_off + len(b'dovi5')] = b'dovi5'
    tm['data'] = bytes(target_data)
    tm['size'] = size
    relsec, _ = elf.relocations[tm['index']]
    target_records = []
    for _, sym, typ, addend in records:
        name = elf.symbols[sym]['name']
        require(name in offsets and typ == 257 and addend == 0, 'unexpected source module pointer')
        target_records.append((offsets[name], sym, 257, 0))
    relsec['data'] = b''.join(RELA.pack(loc, (sym << 32) | typ, addend) for loc, sym, typ, addend in target_records)
    for sec_index, (_, records) in elf.relocations.items():
        if sec_index == tm['index']:
            continue
        require(all(elf.symbols[sym]['index'] != tm['index'] for _, sym, _, _ in records), 'source module struct referenced externally')
    for sym in elf.symbols:
        if sym['index'] == tm['index']:
            require(sym['value'] == 0 and sym['name'] in ('', '$d.3', '__this_module'), 'unexpected source module struct symbol')
            if sym['name'] == '__this_module':
                sym['size'] = size
        if sym['name'] in RENAME:
            require(sym['index'] == 0, 'registration symbol unexpectedly defined')
            sym['name'] = RENAME[sym['name']]
    guard_index = len(elf.symbols)
    elf.symbols.append(dict(name=GUARD, info=0x10, other=0, index=0, value=0, size=0))
    text = elf.section('.text', 1)
    relsec, records = elf.relocations[text['index']]
    text_data = bytearray(text['data'])
    require(len(profile) == 100, 'wrong canary profile count')
    original_mrs = {i for i in range(0, len(text_data), 4)
                    if struct.unpack_from('<I', text_data, i)[0] & 0xffffffe0 == 0xd5384100}
    original_loads = {i for i in range(0, len(text_data), 4)
                      if struct.unpack_from('<I', text_data, i)[0] & 0xffc00000 == 0xf9400000 and
                      ((struct.unpack_from('<I', text_data, i)[0] >> 10) & 4095) == 201}
    require({entry['adrp_text_offset'] for entry in profile} == original_mrs and
            {entry['ldr_text_offset'] for entry in profile} == original_loads,
            'canary profile does not cover every source task access')
    used = {loc for loc, _, _, _ in records}
    new_records = list(records)
    seen = set()
    for pair in profile:
        first, second = pair['adrp_text_offset'], pair['ldr_text_offset']
        require(first % 4 == 0 and second % 4 == 0 and first < second <= first + 48,
                'invalid canary pair instruction ordering')
        require(first not in seen and second not in seen and first not in used and second not in used, 'canary relocation overlap')
        seen.update((first, second))
        a, b = struct.unpack_from('<I', text_data, first)[0], struct.unpack_from('<I', text_data, second)[0]
        require(a == pair['original_adrp_word'] and b == pair['original_ldr_word'], 'canary instruction profile mismatch')
        require(a & 0xffffffe0 == 0xd5384100 and b & 0xffc00000 == 0xf9400000 and
                (a & 31) == ((b >> 5) & 31) and ((b >> 10) & 4095) == 201,
                'invalid profiled canary instruction pair')
        struct.pack_into('<I', text_data, first, 0x90000000 | (a & 31))
        struct.pack_into('<I', text_data, second, b & ~(4095 << 10))
        new_records.extend(((first, guard_index, 275, 0), (second, guard_index, 286, 0)))
    text['data'] = bytes(text_data)
    relsec['data'] = b''.join(RELA.pack(loc, (sym << 32) | typ, addend)
                             for loc, sym, typ, addend in sorted(new_records))
    elf.byname['__versions']['data'] = b''.join(struct.pack('<Q', versions[name][0]) +
        name.encode('ascii') + bytes(56 - len(name)) for name in sorted(imports | {'module_layout'}))
    require(all(len(n) < 56 for n in imports | {'module_layout'}), 'version symbol name too long')
    # ____versions is a private object covering the old section. Update its range too.
    for sym in elf.symbols:
        if sym['index'] == elf.byname['__versions']['index'] and sym['size']:
            require(sym['value'] == 0, 'unexpected version section object offset')
            sym['size'] = len(elf.byname['__versions']['data'])
    result = elf.encode()
    checked = ELF(result)
    require(checked.imports() == imports and checked.versions().keys() == imports | {'module_layout'}, 'canonical output imports/versions incomplete')
    return result


def validate_prepared(source, output, reference, versions, exports, profile):
    expected = canonical(source, reference, versions, exports, profile)
    require(output == expected, 'prepared output differs from canonical target-bound module')
    return digest(expected)


def safe_directory(path):
    absolute = Path(path).absolute()
    for part in [*reversed(absolute.parents), absolute]:
        require(not part.is_symlink() and part.is_dir(), 'unsafe output directory: ' + str(part))


def atomic_write(path, data, source=None):
    path = Path(path)
    safe_directory(path.parent)
    if source is not None:
        require(Path(source).absolute() != path.absolute(), 'output equals source')
    if path.exists() or path.is_symlink():
        st = path.lstat()
        require(stat.S_ISREG(st.st_mode) and st.st_nlink == 1, 'unsafe existing output')
        if source is not None:
            require(not os.path.samefile(source, path), 'output aliases source inode')
    fd, tmp = tempfile.mkstemp(prefix='.dovi5-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        d = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(d)
        finally:
            os.close(d)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('patch', 'validate'))
    parser.add_argument('source')
    parser.add_argument('output')
    parser.add_argument('--ref', required=True)
    parser.add_argument('--versions', required=True)
    parser.add_argument('--ksyms', required=True)
    parser.add_argument('--profile', default=str(Path(__file__).with_name('canary-profile.json')))
    args = parser.parse_args()
    source = read_regular(args.source, allow_links=True)
    output = canonical(source, read_module_reference(args.ref), read_regular(args.versions), kernel_exports(args.ksyms),
                       json.loads(read_regular(args.profile)))
    if args.command == 'patch':
        atomic_write(args.output, output, args.source)
    else:
        require(read_regular(args.output) == output, 'prepared output differs from canonical target-bound module')
    print('OUTPUT_SHA=' + digest(output))


if __name__ == '__main__':
    try:
        main()
    except (Rejected, OSError, ValueError, struct.error) as exc:
        raise SystemExit('dovi5: rejected: ' + str(exc))
