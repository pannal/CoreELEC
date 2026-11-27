# SPDX-License-Identifier: GPL-2.0-or-later
"""Default-off graphics reference patch for the supported exact-source library."""
import hashlib
import json
import struct

# Pinned alongside the maintainer assembly/profile; no target assembler is used.
PROFILE_SHA256 = '62c52a02e87d8ab6c846b7b44a82ab0d8e8dd6e1687975067381f82cb23ecac3'
CAPABILITY = b' [p3i-gfxref:2]'
SITES = ((0x3af0,0xd503233f,'p3i_entry'),
         (0xba30,0x35000508,'p3i_ba30'),
         (0xbdd8,0x34002628,'p3i_bdd8'),
         (0xc1a0,0x2a1f03fa,'p3i_concat_done'),
         (0x515c,0x2a1503e0,'p3i_cp_done'))

def apply_graphics(elf,profile_data,require,rela):
    require(hashlib.sha256(profile_data).hexdigest()==PROFILE_SHA256,
            'unsupported graphics profile fingerprint')
    profile=json.loads(profile_data)
    require(profile['version']==2 and profile['state_size']==32,
            'unsupported graphics protocol')
    payload=bytes.fromhex(profile['text'])
    text=elf.section('.text',1)
    bss=elf.section('.bss',8)
    text_symbol=next(i for i,s in enumerate(elf.symbols)
                     if s['index']==text['index'] and s['info']==3)
    bss_symbol=next(i for i,s in enumerate(elf.symbols)
                    if s['index']==bss['index'] and s['info']==3)
    start=(len(text['data'])+15)&~15
    state=(bss['size']+7)&~7
    bss['size']=state+profile['state_size']
    body=bytearray(text['data'])+bytes(start-len(text['data']))+payload
    labels={name:start+value['offset'] for name,value in profile['labels'].items()}
    references={name:(text_symbol,off) for name,off in labels.items()}
    references.update({
        'p3i_state':(bss_symbol,state),
        'p3i_resume_cp':(text_symbol,0x3af4),
        'p3i_resume_ba30':(text_symbol,0xba34),
        'p3i_taken_ba30':(text_symbol,0xbad0),
        'p3i_resume_bdd8':(text_symbol,0xbddc),
        'p3i_taken_bdd8':(text_symbol,0xc29c),
        'p3i_resume_concat':(text_symbol,0xc1a4),
        'p3i_resume_done':(text_symbol,0x5160),
        'CreateToneCurve':(elf.symbol('CreateToneCurve')[0],0)})
    relsec,old_records=elf.relocations[text['index']]
    records=list(old_records)
    occupied={off for off,_,_,_ in records}
    for off,original,target in SITES:
        require(struct.unpack_from('<I',body,off)[0]==original and off not in occupied,
                'graphics instruction/relocation mismatch')
        struct.pack_into('<I',body,off,0x14000000)
        records.append((off,text_symbol,282,labels[target]))
    original=[r for r in records if r[0]==0xc184]
    require(len(original)==1 and original[0][1:]==
            (elf.symbol('CreateToneCurve')[0],283,0),'graphics curve call mismatch')
    records.remove(original[0])
    records.append((0xc184,text_symbol,283,labels['p3i_curve']))
    for r in profile['relocations']:
        require(r['symbol'] in references,'unknown graphics relocation symbol')
        sym,addend=references[r['symbol']]
        records.append((start+r['offset'],sym,r['type'],addend+r['addend']))
    text['data']=bytes(body)
    relsec['data']=b''.join(rela.pack(off,(sym<<32)|typ,add)
                            for off,sym,typ,add in sorted(records))
    rodata=elf.section('.rodata',1)
    require(rodata['data'][0x14a9:0x14b2]==b' %s %s%s\0','graphics version format mismatch')
    format_offset=len(rodata['data'])
    rodata['data']+=b' %s %s%s'+CAPABILITY+b'\0'
    initrel,initrecords=elf.relocations[elf.section('.init.text')['index']]
    updated=[]
    count=0
    for off,sym,typ,add in initrecords:
        if off in (0xb0,0xb4):
            require(elf.symbols[sym]['index']==rodata['index'] and add==0x14a9
                    and typ in (275,277),'graphics version relocation mismatch')
            add=format_offset
            count+=1
        updated.append((off,sym,typ,add))
    require(count==2,'graphics version relocation pair missing')
    initrel['data']=b''.join(rela.pack(off,(sym<<32)|typ,add) for off,sym,typ,add in updated)
    for name,off in labels.items():
        elf.symbols.append(dict(name=name,info=0x12,other=0,index=text['index'],
                                value=off,size=profile['labels'][name]['size']))
    elf.symbols.append(dict(name='p3i_state',info=0x11,other=0,index=bss['index'],
                            value=state,size=profile['state_size']))
