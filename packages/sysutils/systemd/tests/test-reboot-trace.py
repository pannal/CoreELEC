#!/usr/bin/env python3
"""Exercise final integrated readers, activation/logger and reboot call boundaries.

OS/MMIO boundaries are simulated. Never opens a real watchdog or contacts a device.
Run prepare-reboot-inputs.py first; pass its final systemd tree and the live kernel.
"""
import argparse
from pathlib import Path
import runpy
import subprocess
import tempfile

extract=runpy.run_path(str(Path(__file__).with_name('test-watchdog-handover.py')))['function']


def check_binary(code,temporary,label,cc,includes=(),expect_success=True):
    src=temporary/(label+'.c');binary=temporary/label
    src.write_text(code)
    result=subprocess.run([cc,'-std=gnu11','-Wall','-Wextra','-Werror','-Wno-unused-parameter',
                           *['-I'+str(p) for p in includes],str(src),'-o',str(binary)],capture_output=True,text=True)
    assert result.returncode==0,(label,result.stderr)
    result=subprocess.run([str(binary)],capture_output=True,text=True)
    assert (result.returncode==0)==expect_success,(label,result.returncode,result.stderr)
    print(label, 'PASS' if expect_success else 'unsafe mutation rejected')


KERNEL_PRE=r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <sys/types.h>
#define __iomem
#define READ_ONCE(x) (x)
#define CTRL 0
#define TCNT 8
#define BIT(n) (1u<<(n))
#define PAGE_SIZE 4096
#define scnprintf snprintf
typedef uint32_t u32;
struct device { void *data; };
struct device_attribute { int unused; };
struct aml_wdt_dev { void *reg_base; };
struct watchdog_device { struct aml_wdt_dev *data; };
static struct watchdog_device *dev_get_drvdata(struct device *d) { return d->data; }
static struct aml_wdt_dev *watchdog_get_drvdata(struct watchdog_device *w) { return w->data; }
static bool reboot_trace;
static void *reboot_trace_base;
static int reads,logs;
static char message[512];
static uint32_t readl(void *p) { reads++; return *(uint32_t *)p; }
#define pr_emerg(...) do { logs++; snprintf(message,sizeof message,__VA_ARGS__); } while(0)
'''
KERNEL_MAIN=r'''
int main(void) {
 uint32_t regs[]={0x00040009,0,(1234u<<16)|10000u,0xdeadbeef};
 uint32_t saved[4]; char buf[4096];
 struct aml_wdt_dev w={regs}; struct watchdog_device wd={&w}; struct device d={&wd};
 memcpy(saved,regs,sizeof regs); reboot_trace_base=regs;
 aml_wdt_reboot_trace("off"); assert(!logs&&!reads);
 reboot_trace=true; reboot_trace_base=NULL;
 aml_wdt_reboot_trace("unbound"); assert(!logs&&!reads);
 reboot_trace_base=regs; aml_wdt_reboot_trace("reset-smc-entry");
 assert(logs==1&&reads==2);
 assert(strstr(message,"stage=reset-smc-entry ctrl=00040009 enabled=1 tcnt=04d22710 limit=10000 elapsed=1234"));
 assert(!memcmp(saved,regs,sizeof regs));
 regs[0]&=~BIT(18); memcpy(saved,regs,sizeof regs);
 aml_wdt_reboot_trace("disabled"); assert(strstr(message,"enabled=0")); assert(reads==4);
 assert(reboot_watchdog_show(&d,NULL,buf)>0);
 assert(strstr(buf,"ctrl=00000009 enabled=0 tcnt=04d22710 limit=10000 elapsed=1234"));
 assert(reads==6&&logs==2&&!memcmp(saved,regs,sizeof regs));
 return 0;
}
'''
USER_PRE=r'''
#include <assert.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>
#include <stdint.h>
typedef unsigned int ProcCmdlineFlags;
#define PROC_CMDLINE_TRUE_WHEN_MISSING (1u<<4)
#define PROC_CMDLINE_VALUE_OPTIONAL (1u<<1)
#define FLAGS_SET(v,f) (((v)&(f))==(f))
static void free_cleanup(char **p) { free(*p); }
static int reads,opens,closes,writes,initialisations;
static void close_cleanup(int *p) { if (*p>=0) closes++; }
#define _cleanup_free_ __attribute__((cleanup(free_cleanup)))
#define _cleanup_close_ __attribute__((cleanup(close_cleanup)))
static bool arg_reboot_trace;
static char *reboot_trace_boot;
static const char *option;
static int sample_error,boot_error,open_error;
static char message[1024];
static const char *strna(const char *s) { return s?s:"n/a"; }
static bool match(const char *v,const char *const *items) {
 for (;*items;items++) if (!strcasecmp(v,*items)) return true;
 return false;
}
#define STRCASE_IN_SET(v,...) match(v,(const char *const[]){__VA_ARGS__,NULL})
static int proc_cmdline_get_key(const char *key,ProcCmdlineFlags flags,char **value) {
 assert(!strcmp(key,"meson_wdt.reboot_trace")); assert(flags&PROC_CMDLINE_VALUE_OPTIONAL);
 if (!option) return 0;
 *value=strdup(option); return 1;
}
static int read_one_line_file(const char *path,char **value) {
 reads++;
 if (!strcmp(path,"/proc/sys/kernel/random/boot_id")) {
  if (boot_error) return boot_error;
  *value=strdup("11111111-1111-1111-1111-111111111111"); return 0;
 }
 assert(!strcmp(path,"/sys/bus/platform/drivers/meson_wdt/ffd0f0d0.watchdog/reboot_watchdog"));
 if (sample_error) return sample_error;
 *value=strdup("ctrl=00040009 enabled=1 tcnt=04d22710 limit=10000 elapsed=1234"); return 0;
}
static int fake_open(const char *path,int flags) {
 assert(!strcmp(path,"/dev/kmsg")); assert(flags==(O_WRONLY|O_NOCTTY|O_CLOEXEC));
 opens++; return open_error?-1:17;
}
static int fake_dprintf(int fd,const char *format,...) {
 assert(fd==17); writes++;
 va_list args;va_start(args,format);int r=vsnprintf(message,sizeof message,format,args);va_end(args);return r;
}
static void init_watchdog(void) { initialisations++; }
#define open fake_open
#define dprintf fake_dprintf
'''
USER_MAIN=r'''
static void reset(const char *value) {
 free(reboot_trace_boot);reboot_trace_boot=NULL;arg_reboot_trace=false;option=value;
 reads=opens=closes=writes=initialisations=sample_error=boot_error=open_error=0;message[0]=0;
}
int main(void) {
 reset(NULL);activate();assert(!arg_reboot_trace&&!reads&&!opens&&!writes&&initialisations==1);
 reset("0");activate();assert(!arg_reboot_trace&&!reads&&!opens&&!writes);
 reset("invalid");activate();assert(!arg_reboot_trace&&!reads&&!opens&&!writes);
 reset("1");activate();assert(arg_reboot_trace&&reads==2&&opens==1&&writes==1&&closes==1);
 assert(!strcmp(message,"<0>reboot-watchdog v1 boot=11111111-1111-1111-1111-111111111111 stage=shutdown-watchdog-ready ctrl=00040009 enabled=1 tcnt=04d22710 limit=10000 elapsed=1234 error=0\n"));
 reset("1");sample_error=-ENOENT;activate();assert(arg_reboot_trace&&reads==2&&closes==1);
 assert(strstr(message,"stage=shutdown-watchdog-ready n/a error=-2"));assert(!strstr(message,"enabled=0"));
 reset("1");boot_error=-ENOENT;activate();assert(strstr(message,"boot=n/a"));
 reset("1");open_error=1;activate();assert(opens==1&&!writes&&!closes);
 free(reboot_trace_boot); return 0;
}
'''
CALLER_PRE=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint32_t u32;typedef uint64_t u64;
#define SYS_RESTART 1
#define SYSTEM_RESTART 1
#define KMSG_DUMP_RESTART 1
static int reboot_notifier_list,system_state;
static char order[2048];
static void record(const char *s) { assert(strlen(order)+strlen(s)+2<sizeof order);strcat(order,s);strcat(order,";"); }
#ifdef TRACE_CONFIG_OFF
#include "linux/amlogic/watchdog.h"
#else
static void aml_wdt_reboot_trace(const char *stage) { record(stage); }
#endif
static void blocking_notifier_call_chain(int *p,int event,char *cmd) { assert(p==&reboot_notifier_list&&event==SYS_RESTART&&!cmd);record("notifier-call"); }
static void usermodehelper_disable(void) { record("helper-disable"); }
static void device_shutdown(void) { record("device-call"); }
static void migrate_to_reboot_cpu(void) { record("migrate-call"); }
static void syscore_shutdown(void) { record("syscore-call"); }
#define pr_emerg(...) record("restart-print")
static void kmsg_dump(int reason) { assert(reason==KMSG_DUMP_RESTART);record("dump-call"); }
static void machine_restart(char *cmd) { assert(!cmd);record("machine-call"); }
static u32 psci_function_id_restart;
static u32 parse_reason(const char *cmd) { assert(!cmd);return 1; }
static void meson_smc_restart(u64 id,u64 reason) { assert(id==0x84000009&&reason==1);record("smc-call"); }
'''
CALLER_MAIN=r'''
int main(void) {
 kernel_restart(NULL);assert(system_state==SYSTEM_RESTART);
#ifdef TRACE_CONFIG_OFF
 assert(!strcmp(order,"notifier-call;helper-disable;device-call;migrate-call;syscore-call;restart-print;dump-call;machine-call;"));
#else
 assert(!strcmp(order,"kernel-restart-entry;notifier-call;notifiers-return;helper-disable;device-shutdown-entry;device-call;device-shutdown-return;migrate-call;syscore-entry;syscore-call;syscore-return;restart-print;dump-call;machine-call;"));
#endif
 order[0]=0;psci_function_id_restart=0;meson_common_restart(0,NULL);assert(!order[0]);
 order[0]=0;psci_function_id_restart=0x84000009;meson_common_restart(0,NULL);
#ifdef TRACE_CONFIG_OFF
 assert(!strcmp(order,"smc-call;"));
#else
 assert(!strcmp(order,"reset-smc-entry;smc-call;reset-smc-return;"));
#endif
 return 0;
}
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--systemd-tree',type=Path,required=True)
    parser.add_argument('--kernel-tree',type=Path,required=True)
    parser.add_argument('--cc',default='cc')
    parser.add_argument('--mutations',action='store_true')
    args=parser.parse_args()
    kernel=args.kernel_tree;systemd=args.systemd_tree
    wdt=(kernel/'drivers/amlogic/watchdog/meson_wdt.c').read_text()
    reboot=(kernel/'kernel/reboot.c').read_text()
    smc=(kernel/'drivers/amlogic/reboot/reboot.c').read_text()
    shutdown=(systemd/'src/shutdown/shutdown.c').read_text()
    proc=(systemd/'src/basic/proc-cmdline.c').read_text()
    parse=(systemd/'src/basic/parse-util.c').read_text()
    header=(kernel/'include/linux/amlogic/watchdog.h').read_text()
    helpers=extract(wdt,'aml_wdt_reboot_trace')+extract(wdt,'reboot_watchdog_show')
    logger=extract(shutdown,'reboot_trace')
    for forbidden in ('writel(', 'reset_watchdog(', 'enable_watchdog(', 'set_watchdog_cnt(', 'mutex_lock(', 'kmalloc('):
        assert forbidden not in helpers,forbidden
    assert '/dev/watchdog' not in logger
    assert 'static bool reboot_trace;' in wdt and 'module_param(reboot_trace, bool, 0400);' in wdt
    assert 'static bool arg_reboot_trace;' in shutdown
    assert 'if (reboot_trace) {\n\t\tWRITE_ONCE(reboot_trace_base, wdev->reg_base);' in wdt
    assert 'static DEVICE_ATTR_RO(reboot_watchdog);' in wdt
    remove=extract(wdt,'aml_wdt_remove')
    assert remove.index('device_remove_file')<remove.index('WRITE_ONCE(reboot_trace_base, NULL)')<remove.index('aml_wdt_stop')
    assert '#ifdef CONFIG_AMLOGIC_WDT_MESON' in header
    notify=extract(wdt,'aml_wtd_reboot_notify')
    assert notify.index('"notifier-entry"')<notify.index('hrtimer_cancel')
    assert notify.index('set_watchdog_cnt')<notify.index('reset_watchdog(wdev)')<notify.index('enable_watchdog(wdev)')<notify.index('"notifier-armed"')
    start=shutdown.index('        (void) proc_cmdline_get_bool("meson_wdt.reboot_trace"')
    end=shutdown.index('        reboot_trace("shutdown-watchdog-ready");',start)+len('        reboot_trace("shutdown-watchdog-ready");')
    activation='static void activate(void) {\n'+shutdown[start:end]+'\n}\n'
    userspace=USER_PRE+extract(parse,'parse_boolean')+extract(proc,'proc_cmdline_get_bool')+logger+activation+USER_MAIN
    callers=extract(reboot,'kernel_restart_prepare')+extract(reboot,'kernel_restart')+extract(smc,'meson_common_restart')
    # Exact final source placement around each required userspace operation.
    for prefix in ('initial','final'):
        begin=shutdown.index(f'"{prefix}-sync-entry"');finish=shutdown.index(f'"{prefix}-sync-return"')
        assert begin<shutdown.index('sync_with_progress();',begin)<finish
    assert shutdown.index('"final-cleanup-entry"')<shutdown.index('watchdog_close(/* disarm= */ false)')<shutdown.index('watchdog_free_device();')<shutdown.index('"final-cleanup-return"')
    assert shutdown.index('"final-sync-return"')<shutdown.index('"reboot-dispatch"')<shutdown.index('        switch (cmd) {')
    with tempfile.TemporaryDirectory(prefix='integrated-reboot-trace-') as tmp:
        temporary=Path(tmp)
        check_binary(KERNEL_PRE+helpers+KERNEL_MAIN,temporary,'passive-readers',args.cc)
        check_binary(userspace,temporary,'activation-and-logger',args.cc)
        check_binary(CALLER_PRE+callers+CALLER_MAIN,temporary,'restart-callers',args.cc)
        check_binary('#define TRACE_CONFIG_OFF\n'+CALLER_PRE+callers+CALLER_MAIN,temporary,'disabled-config-callers',args.cc,[kernel/'include'])
        if args.mutations:
            bad=helpers.replace('if (!reboot_trace || !base)','if (!base)',1)
            assert bad!=helpers
            check_binary(KERNEL_PRE+bad+KERNEL_MAIN,temporary,'mutant-no-kernel-gate',args.cc,expect_success=False)
            bad=helpers.replace('BIT(18)','BIT(17)');assert bad!=helpers
            check_binary(KERNEL_PRE+bad+KERNEL_MAIN,temporary,'mutant-wrong-enable-bit',args.cc,expect_success=False)
            bad=userspace.replace('if (!arg_reboot_trace)','if (false)',1);assert bad!=userspace
            check_binary(bad,temporary,'mutant-no-userspace-gate',args.cc,expect_success=False)
            bad=callers.replace('\taml_wdt_reboot_trace("device-shutdown-return");','',1);assert bad!=callers
            check_binary(CALLER_PRE+bad+CALLER_MAIN,temporary,'mutant-missing-shutdown-boundary',args.cc,expect_success=False)
            bad=callers.replace('if (psci_function_id_restart)','if (true)',1);assert bad!=callers
            check_binary(CALLER_PRE+bad+CALLER_MAIN,temporary,'mutant-unconditional-smc',args.cc,expect_success=False)
    print('PASS: final integrated reader/logger/activation/callers and source placement; simulated boundaries only.')
    print('NOT VERIFIED: link/image, actual pstore capture, hardware expiry or firmware protection.')


if __name__=='__main__':main()
