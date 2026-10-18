#!/usr/bin/env python3
"""Prepare pinned systemd255.4 using the selected p3i CE patch stack; no build.

The output is a fresh local test fixture, never the live CE build tree.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile


def prepare(ce, archive, output):
    package=ce/'packages/sysutils/systemd'
    definition=(package/'package.mk').read_text()
    version=re.search(r'^PKG_VERSION="([^"]+)"',definition,re.M).group(1)
    expected=re.search(r'^PKG_SHA256="([^"]+)"',definition,re.M).group(1)
    assert version=='255.4', 'Recheck this focused fixture for a systemd version change'
    assert hashlib.sha256(archive.read_bytes()).hexdigest()==expected, 'Source archive mismatch'
    for path in (ce/'projects/Amlogic-ce/packages/systemd/package.mk',ce/'projects/Amlogic-ce/devices/Amlogic-ng/packages/systemd/package.mk'):
        assert not path.exists(), f'Package override needs re-evaluation: {path}'
    assert 'PKG_PATCH_DIRS=' not in definition, 'Re-evaluate package-specific patch directories'
    # Match scripts/unpack common, arch, version, project and device loops.
    directories=[package/'patches',package/'patches/arm',
                 package/f'patches/{version}',package/f'patches/{version}/arm',
                 ce/'projects/Amlogic-ce/patches/systemd',
                 ce/'projects/Amlogic-ce/patches/systemd/arm',
                 ce/f'projects/Amlogic-ce/patches/systemd/{version}',
                 ce/'projects/Amlogic-ce/devices/Amlogic-ng/patches/systemd']
    patches=[p for directory in directories for p in sorted(directory.glob('*.patch'))]
    names=[p.name for p in patches]
    assert names.index('systemd-0600-preserve-shutdown-watchdog.patch')<names.index('systemd-0610-reboot-watchdog-trace.patch')
    output.mkdir(parents=True,exist_ok=False)
    with tarfile.open(archive) as source:
        source.extractall(output,filter='data')
    roots=[p for p in output.iterdir() if p.is_dir() and (p/'src/shutdown/shutdown.c').is_file()]
    assert len(roots)==1, 'Unexpected systemd source archive layout'
    tree=roots[0]
    canonical=output/f'systemd-{version}'
    if tree!=canonical:
        tree.rename(canonical)
        tree=canonical
    records=[]
    for patch in patches:
        focused=patch.name in ('systemd-0600-preserve-shutdown-watchdog.patch',
                               'systemd-0610-reboot-watchdog-trace.patch')
        # Match CE's existing patch behavior; require zero fuzz for this change's pair.
        command=['patch','--batch','--forward',*(['--fuzz=0'] if focused else []),'-p1','-i',str(patch.resolve())]
        result=subprocess.run(command,cwd=tree,capture_output=True,text=True)
        record={'patch':str(patch.relative_to(ce)),'sha256':hashlib.sha256(patch.read_bytes()).hexdigest(),
                'zero_fuzz_required':focused,'returncode':result.returncode,'output':result.stdout+result.stderr}
        records.append(record)
        assert result.returncode==0,record
    shutdown=tree/'src/shutdown/shutdown.c'
    text=shutdown.read_text()
    assert text.count('static void reboot_trace(')==1
    assert text.count('watchdog_close(/* disarm= */ false);')==1
    manifest={'source_archive_sha256':expected,'project':'Amlogic-ce','device':'Amlogic-ng','arch':'arm',
              'patches':records,'shutdown_sha256':hashlib.sha256(shutdown.read_bytes()).hexdigest()}
    (output/'package-verification.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(f'PASS: pinned systemd {version}, {len(patches)} patches in CE order, both watchdog patches zero-fuzz; {tree}')
    return tree


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ce-tree',type=Path,default=Path(__file__).resolve().parents[4])
    parser.add_argument('--archive',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    archive=args.archive or args.ce_tree/'sources/systemd/systemd-255.4.tar.gz'
    prepare(args.ce_tree.resolve(),archive.resolve(),args.output.resolve())


if __name__=='__main__':main()
