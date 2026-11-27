#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Encode an assembled graphics-reference.S object; never ships vendor code."""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'scripts'))
import dv5_patch as p

a=argparse.ArgumentParser(description=__doc__)
a.add_argument('object',type=Path)
a.add_argument('--output',type=Path,default=HERE.parent/'scripts/graphics-profile.json')
args=a.parse_args()
elf=p.ELF(args.object.read_bytes())
text=elf.section('.text')
labels={s['name']:dict(offset=s['value'],size=s['size']) for s in elf.symbols
        if s['index']==text['index'] and s['name'].startswith('p3i_')}
records=[dict(offset=off,symbol=elf.symbols[sym]['name'],type=typ,addend=add)
         for off,sym,typ,add in elf.relocations[text['index']][1]]
profile=dict(version=2,state_size=32,text=text['data'].hex(),
             source_sha256=p.digest((HERE/'graphics-reference.S').read_bytes()),
             labels=labels,relocations=records)
data=(json.dumps(profile,sort_keys=True,indent=2)+'\n').encode()
args.output.write_bytes(data)
print('PROFILE_SHA256='+p.digest(data))
