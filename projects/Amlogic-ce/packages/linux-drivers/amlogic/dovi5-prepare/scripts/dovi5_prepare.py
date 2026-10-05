#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fail-closed preparation and independent load validation; never insmod."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile

from dv5_patch import (Rejected, SOURCE_SHA, atomic_write, canonical, digest, kernel_exports,
                       read_regular, require, safe_directory, target_reference)


def config_value(path, value='yes'):
    if not path.exists() and not path.is_symlink():
        return value
    data = read_regular(path)
    require(len(data) <= 65536, 'configuration too large')
    seen = False
    for line in data.decode('ascii').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'ENABLE\s*=\s*(yes|no)\s*(?:#.*)?', line)
        require(match is not None and not seen, 'invalid ENABLE configuration')
        value = match.group(1)
        seen = True
    return value


def unlink_path(path):
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def enabled(root):
    return config_value(root / 'storage/.config/dovi5.conf', config_value(root / 'etc/dovi5.conf')) == 'yes'


def context(root, ref=None, versions=None, ksyms=None, release=None):
    require(enabled(root), 'new module disabled')
    selected = None
    for candidate in [root / 'storage/.config/dovi5.ko', root / 'flash/dovi5.ko', root / 'storage/dovi5.ko']:
        if not candidate.exists() and not candidate.is_symlink():
            continue
        try:
            data = read_regular(candidate, allow_links=True)
            require(digest(data) == SOURCE_SHA, 'unsupported source SHA256')
            selected = candidate, data
            break
        except (Rejected, OSError) as exc:
            print('dovi5: rejected source ' + str(candidate) + ': ' + str(exc), file=sys.stderr)
    require(selected is not None, 'no compatible original new-module source')
    source_path, source = selected
    if ref is None:
        result = subprocess.run(['modinfo', '-n', 'dv_compat_shim'], check=True, text=True, capture_output=True)
        require(len(result.stdout.splitlines()) == 1, 'ambiguous shim reference path')
        ref = Path(result.stdout.strip())
    versions = versions or root / 'usr/lib/coreelec/dovi5-Module.symvers'
    ksyms = ksyms or root / 'proc/kallsyms'
    release = release or os.uname().release
    reference = read_regular(ref)
    _, vermagic, *_ = target_reference(reference)
    require(vermagic.split()[0].decode('ascii') == release, 'shim reference differs from running kernel release')
    symvers = read_regular(versions)
    exports = kernel_exports(ksyms)
    profile_data = read_regular(Path(__file__).with_name('canary-profile.json'))
    result = canonical(source, reference, symvers, exports, json.loads(profile_data))
    manifest = dict(source=digest(source), reference=digest(reference), kernel_release=release,
                    versions=digest(symvers), kernel_exports=digest('\n'.join(sorted(exports)).encode('ascii')),
                    patcher=digest(read_regular(Path(__file__).with_name('dv5_patch.py'))),
                    preparation=digest(read_regular(Path(__file__))), profile=digest(profile_data), output=digest(result))
    manifest_bytes = (json.dumps(manifest, sort_keys=True) + '\n').encode('ascii')
    generation = root / 'storage/.dovi5/generations' / digest(manifest_bytes)
    return dict(source_path=source_path, source=source, result=result, manifest=manifest_bytes,
                generation=generation, output=generation / 'dovi5.ko', stamp=generation / 'state.json')


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def check_generation(ctx):
    safe_directory(ctx['generation'])
    require({p.name for p in ctx['generation'].iterdir()} == {'dovi5.ko', 'state.json'}, 'unexpected prepared generation files')
    require(read_regular(ctx['output']) == ctx['result'] and read_regular(ctx['stamp']) == ctx['manifest'],
            'prepared generation differs from current canonical target')


def validate_load(root=Path('/'), ref=None, versions=None, ksyms=None, release=None):
    """Caller must have loaded the original backend before using the returned path."""
    pathfile = root / 'run/dovi5-path'
    safe_directory(pathfile.parent)
    try:
        ctx = context(root, ref, versions, ksyms, release)
        require(read_regular(pathfile) == (str(ctx['output']) + '\n').encode('ascii'), 'wrong prepared generation marker')
        check_generation(ctx)
        require(enabled(root), 'new module disabled before load validation')
        require(read_regular(ctx['source_path'], allow_links=True) == ctx['source'], 'source changed during load validation')
        return ctx['output']
    except (Rejected, OSError, ValueError, subprocess.SubprocessError):
        unlink_path(pathfile)
        raise


def prepare(root=Path('/'), ref=None, versions=None, ksyms=None, release=None):
    pathfile = root / 'run/dovi5-path'
    safe_directory(pathfile.parent)
    unlink_path(pathfile)
    if not enabled(root):
        return 'disabled; original backend retained'
    state = root / 'storage/.dovi5'
    safe_directory(state.parent)
    if not state.exists() and not state.is_symlink():
        state.mkdir(mode=0o700)
    safe_directory(state)
    generations = state / 'generations'
    if not generations.exists() and not generations.is_symlink():
        generations.mkdir(mode=0o700)
    safe_directory(generations)
    fd = os.open(root / 'run/dovi5-prepare.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        st = os.fstat(fd)
        require(stat.S_ISREG(st.st_mode) and st.st_nlink == 1, 'unsafe preparation lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 'preparation already running; original backend retained'
        try:
            ctx = context(root, ref, versions, ksyms, release)
        except Rejected as exc:
            if str(exc) == 'no compatible original new-module source':
                return str(exc) + '; original backend retained'
            raise
        if ctx['generation'].exists() or ctx['generation'].is_symlink():
            check_generation(ctx)
        else:
            # Output and stamp become visible together, with no symlink pointer or old-generation mutation.
            work = Path(tempfile.mkdtemp(prefix='.prepare-', dir=generations))
            try:
                atomic_write(work / 'dovi5.ko', ctx['result'], ctx['source_path'])
                atomic_write(work / 'state.json', ctx['manifest'])
                fsync_directory(work)
                os.rename(work, ctx['generation'])
                fsync_directory(generations)
            finally:
                if work.exists():
                    shutil.rmtree(work)
            check_generation(ctx)
        require(read_regular(ctx['source_path'], allow_links=True) == ctx['source'], 'source changed during preparation')
        require(enabled(root), 'preparation disabled before publication')
        atomic_write(pathfile, (str(ctx['output']) + '\n').encode('ascii'))
        return 'validated prepared module available; loader still requires original backend'
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', choices=('prepare', 'validate-load'), default='prepare')
    parser.add_argument('--root', type=Path, default=Path('/'), help='fixture root; production uses /')
    parser.add_argument('--ref', type=Path)
    parser.add_argument('--versions', type=Path)
    parser.add_argument('--ksyms', type=Path)
    parser.add_argument('--kernel-release')
    args = parser.parse_args()
    try:
        operation = validate_load if args.command == 'validate-load' else prepare
        result = operation(args.root, args.ref, args.versions, args.ksyms, args.kernel_release)
        if args.command == 'validate-load':
            print(result)
        else:
            print('dovi5: ' + result, file=sys.stderr)
    except (Rejected, OSError, ValueError, subprocess.SubprocessError) as exc:
        print('dovi5: rejected; original backend retained: ' + str(exc), file=sys.stderr)
        return 1 if args.command == 'validate-load' else 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
