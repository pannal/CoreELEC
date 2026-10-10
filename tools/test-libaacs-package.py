#!/usr/bin/env python3
"""Verify libaacs packaging and production key/config lifetimes on the host.

The archive is supplied explicitly; no downloads, target/image/dependency builds
or installation occur here. Crypto/MMC/keycache services record the production
key getters. The actual regenerated parser reads synthetic data through an AACS
file callback. --negative-controls rejects resource/cache/filesystem regressions.
"""
import argparse
import hashlib
import os
from pathlib import Path
import re
import shlex
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TIP = '55be92be9e80a654b7c98d29fec5769b1b1493d9'
ARCHIVE_SHA256 = '1996673a9fc45ee4a364c66ffa84756629bf3923e52346c7358b71becb8e4419'


def function(source, signature):
    start = source.index(signature)
    opening = source.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


def recipe_variables(recipe, directory):
    names = ['PKG_VERSION', 'PKG_SHA256', 'PKG_DEPENDS_TARGET', 'PKG_TOOLCHAIN',
             'PKG_CONFIGURE_OPTS_TARGET']
    command = 'SYSROOT_PREFIX="$1"\nsource "$2"\nprintf "%s\\0" ' + ' '.join(f'"${{{n}}}"' for n in names)
    result = subprocess.check_output(['bash', '-c', command, 'recipe', str(directory), str(recipe)])
    return dict(zip(names, result.decode().split('\0')[:-1]))


def check_install(recipe, source, out):
    # Run the production install hook in its normal out-of-source relationship.
    staged = out / 'stage'
    build = source / '.target-test'
    build.mkdir()
    subprocess.run(['bash', '-c', 'set -eu\nSYSROOT_PREFIX="$1"\nINSTALL="$2"\nsource "$3"\npost_makeinstall_target',
                    'install', str(out / 'sysroot'), str(staged), str(recipe)], cwd=build, check=True)
    installed = staged / 'usr/config/aacs/KEYDB.cfg'
    assert installed.read_bytes() == (source / 'KEYDB.cfg').read_bytes()
    # Execute the existing startup copy against isolated directories twice.
    # Production cp -i must seed a new install and preserve a user's saved file.
    user = out / 'user-config'
    user.mkdir()
    setup = (ROOT / 'packages/sysutils/systemd/scripts/userconfig-setup').read_text()
    setup = setup.replace('/usr/config', str(staged / 'usr/config')).replace('/storage/.config', str(user))
    subprocess.run(['bash', '-c', setup], check=True)
    saved = user / 'aacs/KEYDB.cfg'
    assert saved.read_bytes() == installed.read_bytes()
    saved.write_text('# synthetic user configuration; preserve me\n')
    expected = saved.read_bytes()
    result = subprocess.run(['bash', '-c', setup])
    # GNU cp reports a declined overwrite as status 1 with EOF from false.
    assert result.returncode == 1
    assert saved.read_bytes() == expected


def run_host(code, source, out, name, extra_sources=(), extra_flags=(), negative=False):
    path, binary = out / (name + '.c'), out / name
    path.write_text(code)
    subprocess.run([os.environ.get('HOST_CC', 'gcc'), '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-parameter', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    '-fno-pie', '-no-pie', '-DHAVE_CONFIG_H=0', '-I', str(source / 'src'),
                    '-I', str(out), str(path), *map(str, extra_sources), *extra_flags,
                    '-o', str(binary)], check=True)
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20,
                            env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'})
    if negative:
        assert result.returncode and 'Assertion' in result.stderr, result.stdout + result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr


def key_source(source):
    code = (source / 'src/libaacs/aacs.c').read_text()
    methods = '\n'.join(function(code, signature) for signature in [
        'static config_file *_ensure_config(', 'static const title_entry_list *_disc_config_entry(',
        'static void _keycache_save(', 'static int _calc_mk(', 'static int _get_mk(',
        'static int _read_vid(', 'static int _get_vid(', 'static int _calc_vuk(', 'static int _get_vuk(',
        'int aacs_open_device(', 'void aacs_close('])
    return KEY_PRELUDE + methods + KEY_TESTS


def parser_source(source, out, parser_text=None):
    grammar = out / 'keydbcfg-parser.y'
    grammar.write_text(parser_text or (source / 'src/file/keydbcfg-parser.y').read_text())
    parser = out / 'keydbcfg-parser.c'
    yacc_flags = shlex.split(re.search(r'(?m)^AM_YFLAGS\s*=\s*(.+)$', (source / 'Makefile.am').read_text()).group(1))
    subprocess.run(['bison', *yacc_flags, '--defines=' + str(out / 'keydbcfg-parser.h'),
                    '--output=' + str(parser), str(grammar)], check=True)
    lexer = out / 'lex.yy.c'
    subprocess.run(['flex', str(source / 'src/file/keydbcfg-lexer.l')], cwd=out, check=True)
    config = (source / 'src/file/keydbcfg.c').read_text()
    dirs = (source / 'src/file/dirs_xdg.c').read_text()
    filesystem = (source / 'src/file/filesystem.c').read_text()
    methods = function(filesystem, 'AACS_FILE_OPEN aacs_register_file(')
    methods += '\n' + re.search(r'(?m)^#define USER_CFG_DIR[^\n]+', dirs).group()
    methods += '\n' + re.search(r'(?m)^#define CFG_DIR[^\n]+', config).group()
    methods += '\n' + function(dirs, 'char *file_get_config_home(')
    methods += '\n' + function(config, 'static char *_config_file_user(')
    return PARSER_PRELUDE + '\n' + methods + PARSER_TESTS, [parser, lexer, source / 'src/util/strutl.c']


def main():
    global ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--negative-controls', action='store_true')
    args = parser.parse_args()
    ROOT = args.root.resolve()
    package = ROOT / 'packages/multimedia/libaacs'
    recipe = package / 'package.mk'
    subprocess.run(['bash', '-n', str(recipe)], check=True)
    assert hashlib.sha256(args.archive.read_bytes()).hexdigest() == ARCHIVE_SHA256
    with tempfile.TemporaryDirectory(prefix='libaacs-package-') as tmp:
        out = Path(tmp)
        values = recipe_variables(recipe, out / 'sysroot')
        assert values['PKG_VERSION'] == '0.12.0' and values['PKG_SHA256'] == ARCHIVE_SHA256
        assert values['PKG_DEPENDS_TARGET'] == 'toolchain libgcrypt'
        assert values['PKG_TOOLCHAIN'] == 'autotools' and '--disable-werror' in values['PKG_CONFIGURE_OPTS_TARGET']
        assert TIP in recipe.read_text()
        with tarfile.open(args.archive) as archive:
            archive.extractall(out, filter='data')
        source = out / 'libaacs-0.12.0'
        keydb = (source / 'KEYDB.cfg').read_bytes()
        patches = sorted((package / 'patches').glob('*.patch'))
        assert [p.name for p in patches] == ['libaacs-01-upstream-0.12.0-to-55be92be.patch',
                                           'libaacs-02-fix-mkb-leak-on-missing-config.patch']
        for patch in patches:
            result = subprocess.run(['patch', '-p1', '--batch', '--fuzz=0', '-i', str(patch)],
                                    cwd=source, text=True, capture_output=True, check=True)
            assert 'offset' not in result.stdout and 'fuzz' not in result.stdout
        assert (source / 'KEYDB.cfg').read_bytes() == keydb
        assert 'src/libaacs/mk.c' in (source / 'Makefile.am').read_text()
        check_install(recipe, source, out)
        key = key_source(source)
        run_host(key, source, out, 'key-lifetime')
        parse, inputs = parser_source(source, out)
        flags = ['-Wl,--wrap=malloc', '-Wl,--wrap=calloc', '-Wl,--wrap=realloc',
                 '-Wl,--wrap=strdup', '-Wl,--wrap=free']
        run_host(parse, source, out, 'parser-filesystem', inputs, flags)
        print('PASS: archive/patches, original KEYDB delivery/user preservation, production '
              'MKB and explicit-path/close lifetimes, key fallback/cache policy, actual parser/config filesystem; ASan/UBSan')
        if args.negative_controls:
            mutations = {
                'missing-config MKB leak': ('if (!_ensure_config(aacs)) {\n        mkb_close(mkb);', 'if (!_ensure_config(aacs)) {'),
                'deferred path release lost': ('X_FREE(aacs->configfile_path);\n            aacs->configfile_probed', 'aacs->configfile_probed'),
                'close path release lost': ('X_FREE(aacs->configfile_path);\n\n    uk_free', 'uk_free'),
                'close config release lost': ('keydbcfg_config_file_close(aacs->cf);', ''),
                'explicit path ownership lost': ('configfile_path ? str_dup(configfile_path) : NULL', 'NULL'),
                'repeat missing-config probe': ('aacs->configfile_probed = 1;', ''),
                'config-derived VUK cached': ('if (!aacs->poisoned_keys) {', 'if (1) {'),
                'MK fallback lost': ('memcpy(aacs->mk, ce->entry.mk, sizeof(aacs->mk));', ''),
                'VID fallback lost': ('memcpy(aacs->vid, ce->entry.vid, sizeof(aacs->vid));', ''),
                'VUK fallback lost': ('memcpy(vuk, ce->entry.vuk, 16);', ''),
            }
            for name, (old, new) in mutations.items():
                assert old in key
                run_host(key.replace(old, new, 1), source, out, 'key-mutant', negative=True)
                print('REJECTED:', name)
            grammar = (source / 'src/file/keydbcfg-parser.y').read_text()
            old = '/* not stored, only keep parsing for backward compatibility */\n      X_FREE($3);'
            assert grammar.count(old) == 3
            for occurrence, name in enumerate(('BN', 'PAK', 'TK')):
                pieces = grammar.split(old)
                mutated = old.join(pieces[:occurrence + 1]) + '/* ignored value leaked */' + old.join(pieces[occurrence + 1:])
                parse, inputs = parser_source(source, out, mutated)
                run_host(parse, source, out, 'parser-mutant', inputs, flags, negative=True)
                print('REJECTED:', name + ' ignored-entry leak')
            parse, inputs = parser_source(source, out)
            generated = inputs[0].read_text()
            old = 'AACS_FILE_H *fp = file_open(path, "r");'
            assert old in generated
            inputs[0].write_text(generated.replace(old, 'AACS_FILE_H *fp = NULL;', 1))
            run_host(parse, source, out, 'filesystem-mutant', inputs, flags, negative=True)
            print('REJECTED: config filesystem callback bypass')
            parse, inputs = parser_source(source, out)
            assert 'file_open = p;' in parse
            run_host(parse.replace('file_open = p;', '', 1), source, out,
                     'registration-mutant', inputs, flags, negative=True)
            print('REJECTED: filesystem callback registration lost')


KEY_PRELUDE = r'''
#include <assert.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "file/keydbcfg.h"
#include "libaacs/aacs.h"
typedef unsigned crypto_error_t;
typedef struct {int version;} MKB;
typedef struct {int aacs2;} CONTENT_CERT;
struct aacs {uint8_t mk[16],vid[16],disc_id[20];int no_cache,mkb_version;
 uint8_t poisoned_keys,configfile_probed;char *configfile_path,*path;config_file *cf;CONTENT_CERT *cc;
 void *uk;int bee,bec;};
enum {DBG_AACS=1,DBG_CRIT=2,MMC_READ_VID=3};
static const uint8_t empty_key[32]={0};
static void log_debug(int mask,const char *format,...){(void)mask;(void)format;}
#define BD_DEBUG log_debug
#define LOG_CRYPTO_ERROR(mask,text,error) log_debug(mask,"%s %u",text,error)
static void *owned[16];static int ownedCount,lifeMode,configFreed,titleError;static AACS *closing;
static void *own(void *p){assert(p&&ownedCount<16);owned[ownedCount++]=p;return p;}
static void release(void *p){
 if(p==closing&&p){const unsigned char *bytes=p;for(size_t i=0;i<sizeof(*closing);++i)assert(!bytes[i]);closing=NULL;}
 for(int i=0;i<ownedCount;++i){if(owned[i]==p){owned[i]=owned[--ownedCount];break;}}
 free(p);
}
#define X_FREE(p) do {release(p);(p)=NULL;} while (0)
static char *str_dup(const char *p){return own(strdup(p));}
static CONTENT_CERT *_read_cc_any(AACS *a){(void)a;return own(calloc(1,sizeof(CONTENT_CERT)));}
static int _get_bus_encryption_enabled(AACS *a){(void)a;return 0;}
static int _calc_title_hash(AACS *a){(void)a;return titleError;}
static int _calc_uks(AACS *a){(void)a;return 0;}
static int _get_bus_encryption_capable(AACS *a){(void)a;return 0;}
static int _read_read_data_key(AACS *a){(void)a;return 0;}
static void uk_free(void **p){release(*p);*p=NULL;}
static void cc_free(CONTENT_CERT **p){release(*p);*p=NULL;}
int keydbcfg_config_file_close(config_file *cf){if(cf){assert(lifeMode);++configFreed;release(cf);}return 0;}

static char *str_print_hex(char *out,const uint8_t *in,size_t count){(void)in;memset(out,'0',count*2);out[count*2]=0;return out;}
static config_file config;static title_entry_list entry;
static int configAvailable,configReads,opens,closes,liveMkb,missingMkb,mkResult,vidResult,cryptoResult;
static int cacheHits[3],cacheWrites[3];
static int type_index(const char *type){return !strcmp(type,"mk")?0:!strcmp(type,"vid")?1:2;}
config_file *keydbcfg_config_load(const char *path,const uint8_t *id){(void)id;++configReads;
 if(lifeMode){assert(path&&!strcmp(path,"/explicit/aacs/KEYDB.cfg"));return configAvailable?own(calloc(1,sizeof(config_file))):NULL;}
 return configAvailable?&config:NULL;
}
int keycache_find(const char *type,const uint8_t *id,uint8_t *key,unsigned size){(void)id;int hit=cacheHits[type_index(type)];if(hit)memset(key,0x70,size);return hit;}
int keycache_save(const char *type,const uint8_t *id,const uint8_t *key,unsigned size){(void)id;(void)key;(void)size;++cacheWrites[type_index(type)];return 1;}
static MKB *_mkb_open(AACS *aacs){(void)aacs;if(missingMkb)return NULL;MKB *m=malloc(sizeof(*m));assert(m);m->version=9;++opens;++liveMkb;return m;}
static int mkb_version(MKB *m){return m->version;}
static void mkb_close(MKB *m){assert(m&&liveMkb>0);free(m);++closes;--liveMkb;}
static void _update_rl(MKB *m){assert(m);}
static int mk_calculate(uint8_t *mk,MKB *m,pk_list *pk,dk_list *dk){(void)m;(void)pk;(void)dk;if(!mkResult)memset(mk,0x22,16);return mkResult;}
static int _mmc_read_auth(AACS *a,int type,uint8_t *one,uint8_t *two){(void)a;(void)type;(void)two;if(!vidResult)memset(one,0x33,16);return vidResult;}
static crypto_error_t crypto_aes128d(const uint8_t *key,const uint8_t *in,uint8_t *out){(void)key;(void)in;if(!cryptoResult)memset(out,0x44,16);return cryptoResult;}
static AACS reset(void){assert(!liveMkb&&!ownedCount);lifeMode=configFreed=titleError=0;memset(&config,0,sizeof(config));memset(&entry,0,sizeof(entry));config.list=&entry;
 configAvailable=configReads=opens=closes=missingMkb=mkResult=vidResult=cryptoResult=0;
 memset(cacheHits,0,sizeof(cacheHits));memset(cacheWrites,0,sizeof(cacheWrites));AACS a={0};a.disc_id[0]=entry.entry.discid[0]=0x19;return a;}
'''

KEY_TESTS = r'''
static void lifecycle(void){
 for(int phase=0;phase<3;++phase){
  (void)reset();lifeMode=1;configAvailable=phase==2;titleError=phase==0?AACS_ERROR_CORRUPTED_DISC:0;
  AACS *heap=own(calloc(1,sizeof(*heap)));char caller[]="/explicit/aacs/KEYDB.cfg";
  assert(aacs_open_device(heap,"/synthetic/disc",caller)==titleError);
  assert(heap->configfile_path&&heap->configfile_path!=caller&&!strcmp(heap->configfile_path,caller));
  assert(!configReads&&!heap->configfile_probed);caller[0]='X';
  heap->uk=own(malloc(4));
  if(phase){config_file *cf=_ensure_config(heap);assert((cf!=NULL)==configAvailable);assert(!heap->configfile_path);
   assert(configReads==1&&heap->configfile_probed);assert(_ensure_config(heap)==cf&&configReads==1);}
  closing=heap;aacs_close(heap);assert(!closing&&!ownedCount&&configFreed==(phase==2));
 }
 aacs_close(NULL);
}
int main(void){
 lifecycle();AACS a=reset();assert(_calc_mk(&a)==AACS_ERROR_NO_CONFIG);assert(opens==1&&closes==1&&!liveMkb&&configReads==1);
 assert(_calc_mk(&a)==AACS_ERROR_NO_CONFIG);assert(opens==2&&closes==2&&!liveMkb&&configReads==1);
 a=reset();missingMkb=1;assert(_calc_mk(&a)==AACS_ERROR_CORRUPTED_DISC&&configReads==0&&!liveMkb);
 a=reset();CONTENT_CERT cert={1};a.cc=&cert;assert(_calc_mk(&a)==AACS_ERROR_UNSUPPORTED_DISC);assert(opens==closes&&!liveMkb&&!configReads);
 a=reset();configAvailable=1;mkResult=AACS_ERROR_NO_PK;assert(_calc_mk(&a)==AACS_ERROR_NO_PK);assert(opens==closes&&!liveMkb&&!cacheWrites[0]);
 a=reset();configAvailable=1;assert(_calc_mk(&a)==0&&a.mk[0]==0x22);assert(opens==closes&&!liveMkb&&cacheWrites[0]==1);
 assert(_get_mk(&a)==0&&opens==1);
 a=reset();cacheHits[0]=1;assert(_get_mk(&a)==0&&a.mk[0]==0x70&&!opens&&!configReads);
 a=reset();configAvailable=1;mkResult=AACS_ERROR_NO_PK;entry.entry.mk[0]=0x21;
 assert(_get_mk(&a)==0&&a.mk[0]==0x21&&a.poisoned_keys&&opens==closes&&!cacheWrites[0]);
 a=reset();configAvailable=1;vidResult=AACS_ERROR_NO_CERT;entry.entry.vid[0]=0x31;
 assert(_get_vid(&a)==0&&a.vid[0]==0x31&&a.poisoned_keys&&!cacheWrites[1]);
 a=reset();configAvailable=1;mkResult=AACS_ERROR_NO_PK;entry.entry.vuk[0]=0x41;uint8_t vuk[16]={0};
 assert(_get_vuk(&a,vuk)==0&&vuk[0]==0x41&&a.poisoned_keys&&!cacheWrites[2]&&!liveMkb);
 a=reset();configAvailable=1;mkResult=AACS_ERROR_NO_PK;entry.entry.mk[0]=0x21;entry.entry.discid[0]=0x99;
 assert(_get_mk(&a)==AACS_ERROR_NO_PK&&!a.poisoned_keys&&!a.mk[0]&&!liveMkb);
 a=reset();configAvailable=1;mkResult=AACS_ERROR_NO_PK;entry.entry.mk[0]=0x21;memset(vuk,0,16);
 assert(_calc_vuk(&a,vuk)==0&&vuk[0]==(0x44^0x33)&&a.poisoned_keys&&!cacheWrites[2]&&!liveMkb);
 a=reset();configAvailable=1;memset(vuk,0,16);assert(_calc_vuk(&a,vuk)==0&&!a.poisoned_keys);
 assert(cacheWrites[0]==1&&cacheWrites[1]==1&&cacheWrites[2]==1&&!liveMkb);
 a=reset();configAvailable=1;a.no_cache=1;memset(vuk,0,16);assert(_calc_vuk(&a,vuk)==0);
 assert(!cacheWrites[0]&&!cacheWrites[1]&&!cacheWrites[2]&&!liveMkb);
 a=reset();configAvailable=1;cryptoResult=1;memset(vuk,0,16);assert(_calc_vuk(&a,vuk)==AACS_ERROR_UNKNOWN&&!cacheWrites[2]&&!liveMkb);
 a=reset();vuk[0]=0x11;assert(_get_vuk(&a,vuk)==0&&!opens&&!configReads);
 a=reset();cacheHits[2]=1;memset(vuk,0,16);assert(_get_vuk(&a,vuk)==0&&vuk[0]==0x70&&!opens&&!configReads);
}
'''

PARSER_PRELUDE = r'''
#include <assert.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "file/keydbcfg.h"
#include "file/file.h"
#include "util/strutl.h"
#include "util/logging.h"
#include "util/macro.h"
uint32_t debug_mask=0;
void bd_debug(const char *file,int line,uint32_t mask,const char *format,...){(void)file;(void)line;(void)mask;(void)format;}
void *__real_malloc(size_t);void *__real_calloc(size_t,size_t);void *__real_realloc(void*,size_t);char *__real_strdup(const char*);void __real_free(void*);
static void *tracked[8192];static int outstanding;
static void remember(void *p){if(!p)return;for(size_t i=0;i<8192;++i)if(!tracked[i]){tracked[i]=p;++outstanding;return;}assert(0);}
static void forget(void *p){if(!p)return;for(size_t i=0;i<8192;++i)if(tracked[i]==p){tracked[i]=NULL;--outstanding;return;}}
void *__wrap_malloc(size_t n){void *p=__real_malloc(n);remember(p);return p;}
void *__wrap_calloc(size_t n,size_t s){void *p=__real_calloc(n,s);remember(p);return p;}
void *__wrap_realloc(void *old,size_t n){void *p=__real_realloc(old,n);if(p){forget(old);remember(p);}return p;}
char *__wrap_strdup(const char *s){char *p=__real_strdup(s);remember(p);return p;}
void __wrap_free(void *p){forget(p);__real_free(p);}
static const char *input;static size_t cursor;static int opened,closed,reads;
static int64_t memory_read(AACS_FILE_H *fp,uint8_t *buf,int64_t size){(void)fp;++reads;size_t n=strlen(input)-cursor;if(n>(size_t)size)n=size;memcpy(buf,input+cursor,n);cursor+=n;return n;}
static void memory_close(AACS_FILE_H *fp){++closed;free(fp);}
static AACS_FILE_H *memory_open(const char *path,const char *mode){assert(!strcmp(path,"/virtual/aacs/KEYDB.cfg")&&!strcmp(mode,"r"));
 ++opened;cursor=0;AACS_FILE_H *fp=calloc(1,sizeof(*fp));assert(fp);fp->read=memory_read;fp->close=memory_close;return fp;}
AACS_FILE_H *(*file_open)(const char*,const char*)=NULL;
'''

PARSER_TESTS = r'''
int main(void){
 assert(aacs_register_file(memory_open)==NULL&&file_open==memory_open);
 unsetenv("AACS_HOME");unsetenv("XDG_CONFIG_HOME");setenv("HOME","/storage",1);
 char *path=_config_file_user("KEYDB.cfg");assert(!strcmp(path,"/storage/.config/aacs/KEYDB.cfg"));free(path);
 setenv("XDG_CONFIG_HOME","/xdg",1);path=_config_file_user("KEYDB.cfg");assert(!strcmp(path,"/xdg/aacs/KEYDB.cfg"));free(path);
 setenv("AACS_HOME","/override",1);path=_config_file_user("KEYDB.cfg");assert(!strcmp(path,"/override/aacs/KEYDB.cfg"));free(path);
 assert(!outstanding);
 const char *valid="0x1919191919191919191919191919191919191919 = synthetic | V | 0x11111111111111111111111111111111\n";
 const char *ignored="0x1919191919191919191919191919191919191919 = synthetic | B | 1-0x11111111111111111111111111111111 | P | 1-0x11111111111111111111111111111111 | T | 1-0x11111111111111111111111111111111 | V | 0x11111111111111111111111111111111\n";
 const char *recovered="malformed entry\n0x1919191919191919191919191919191919191919 = synthetic | V | 0x11111111111111111111111111111111\n";
 for(int i=0;i<100;++i){config_file config={0};input=i%3==0?valid:i%3==1?ignored:recovered;
  assert(keydbcfg_parse_config(&config,"/virtual/aacs/KEYDB.cfg",NULL,1));
  assert(config.list&&config.list->entry.vuk[0]==0x11);assert(opened==closed&&reads>0);
  config_file *owned=malloc(sizeof(config));assert(owned);*owned=config;
  assert(keydbcfg_config_file_close(owned));assert(!outstanding);
 }
}
'''


if __name__ == '__main__':
    main()
