#!/usr/bin/env python3
"""Syntax-check affected libCEC units with existing CE compilation commands.

Requires configured-source env.h and include/version.h from that CE build.
Does not configure, link or build an image/package.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--compile-commands', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    entries = json.loads(args.compile_commands.read_text())
    env = dict(os.environ, CCACHE_DISABLE='1', CCACHE_TEMPDIR=str(out))
    receipts = []
    for relative in ('src/libcec/CECClient.cpp',
                     'src/libcec/implementations/CECCommandHandler.cpp',
                     'src/libcec/implementations/RLCommandHandler.cpp',
                     'src/libcec/implementations/SLCommandHandler.cpp'):
        entry = next(e for e in entries if e['file'].endswith('/' + relative))
        original = entry['file'][:-len(relative)].rstrip('/')
        original_args = shlex.split(entry['command'])
        command = []
        index = 0
        while index < len(original_args):
            value = original_args[index]
            if value == '-o':
                index += 2
                continue
            if value != '-c':
                command.append(value.replace(original, str(source)))
            index += 1
        command.append('-fsyntax-only')
        run = subprocess.run(command, cwd=entry['directory'], env=env,
                             capture_output=True, text=True, timeout=60)
        receipts.append(dict(file=relative, command=command, cwd=entry['directory'],
                             returncode=run.returncode, stdout=run.stdout,
                             stderr=run.stderr))
        print(relative, 'PASS' if run.returncode == 0 else 'FAIL', flush=True)
        if run.returncode:
            print(run.stderr, flush=True)
    (out / 'ce-syntax.json').write_text(json.dumps(receipts, indent=2) + '\n')
    return int(any(r['returncode'] for r in receipts))


if __name__ == '__main__':
    raise SystemExit(main())
