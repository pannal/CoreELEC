#!/usr/bin/env python3
"""Replay actual retained CE flags against final integrated inputs, syntax only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess


def trim(args):
    result=[];skip=False
    for arg in args:
        if skip:skip=False;continue
        if arg in ('-o','-MF','-MT','-MQ'):skip=True;continue
        if arg in ('-c','-MD','-MMD') or arg.startswith('-Wp,-MD,'):continue
        result.append(arg)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-root',type=Path,required=True)
    parser.add_argument('--systemd-tree',type=Path,required=True)
    parser.add_argument('--kernel-tree',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    build=args.build_root.resolve();systemd=args.systemd_tree.resolve();kernel=args.kernel_tree.resolve()
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    original_systemd=build/'build/systemd-255.4'
    original_kernel=build/'build/linux-amlogic-4.9-20'
    actual_header=kernel/'include/linux/amlogic/watchdog.h'
    overlay=output/'include'
    exposed=overlay/'linux/amlogic/watchdog.h';exposed.parent.mkdir(parents=True)
    exposed.symlink_to(actual_header)
    assert exposed.resolve()==actual_header
    env=dict(os.environ,CCACHE_DISABLE='1',PATH=str(build/'toolchain/bin')+':'+os.environ['PATH'])
    records=[]
    def run(label,command,cwd,source,header_required=False):
        result=subprocess.run(command,cwd=cwd,env=env,capture_output=True,text=True)
        (output/(label+'.stdout')).write_text(result.stdout)
        (output/(label+'.stderr')).write_text(result.stderr)
        records.append({'label':label,'directory':str(cwd),'arguments':command,'returncode':result.returncode,
                        'source':str(source),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest()})
        print(label,'rc='+str(result.returncode),flush=True)
        assert result.returncode==0,(label,result.stderr)
        if header_required:
            assert str(exposed) in result.stderr, 'Compiler did not include integrated header'
    entries=json.loads((original_systemd/'.armv8a-libreelec-linux-gnueabihf/compile_commands.json').read_text())
    selected=('src/shutdown/shutdown.c','src/shared/watchdog.c','src/core/main.c')
    systemd_count=0
    for i,entry in enumerate(entries):
        relative=next((p for p in selected if entry['file'].endswith(p)),None)
        if relative:
            source=systemd/relative;command=trim(shlex.split(entry['command']))
            assert command.count(entry['file'])==1
            command=[str(source) if x==entry['file'] else x for x in command]+['-fsyntax-only']
            run('systemd-final-'+str(i),command,Path(entry['directory']),source)
            systemd_count+=1
    assert systemd_count==5, f'Review changed systemd command set: {systemd_count}'
    driver_args=None
    for name in ('drivers/amlogic/watchdog/meson_wdt.c','kernel/reboot.c','drivers/amlogic/reboot/reboot.c'):
        path=Path(name);record=original_kernel/path.parent/('.'+path.stem+'.o.cmd')
        command=trim(shlex.split(record.read_text().splitlines()[0].split(' := ',1)[1]))
        source=kernel/name
        assert command.count(name)==1
        command=[command[0],'-I'+str(overlay)]+[str(source) if x==name else x for x in command[1:]]
        if name.endswith('meson_wdt.c'):driver_args=command
        run('kernel-final-'+path.parent.name+'-'+path.stem,command+['-H','-fsyntax-only'],original_kernel,source,True)
    stub=output/'disabled-config.c'
    stub.write_text('#undef CONFIG_AMLOGIC_WDT_MESON\n#include <linux/amlogic/watchdog.h>\nvoid reboot_trace_disabled_check(void) { aml_wdt_reboot_trace("off"); }\n')
    command=[str(stub) if x==str(kernel/'drivers/amlogic/watchdog/meson_wdt.c') else x for x in driver_args]
    run('kernel-disabled-header',command+['-H','-fsyntax-only'],original_kernel,stub,True)
    manifest={'header':str(actual_header),'header_sha256':hashlib.sha256(actual_header.read_bytes()).hexdigest(),
              'header_overlay_resolves_to_actual':True,'commands':records}
    (output/'ce-syntax-commands.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(f'PASS: {len(records)} actual CE syntax variants against final integrated inputs; no object/link/image build.')


if __name__=='__main__':main()
