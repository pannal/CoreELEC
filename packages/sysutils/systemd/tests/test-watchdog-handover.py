#!/usr/bin/env python3
"""Host actual-body regression; never opens a watchdog or contacts a device.

Usage: test-watchdog-handover.py --systemd-tree UNPACKED_255_4 --kernel-tree KERNEL
Use the final package-prepared source, including the diagnostic patch.
prepare-reboot-inputs.py checks the complete actual package application separately.
MMIO, watchdog framework and OS boundaries are simulated, not device verification.
"""
import argparse
import pathlib
import re
import subprocess
import tempfile


def function(source, name):
    match = re.search(r'^([^\n;]*\b' + name + r'\([^;]*?\)\s*\{)', source, re.M)
    if not match:
        raise AssertionError(f'missing function {name}')
    start = match.start()
    pos = match.end()
    depth = 1
    while depth:
        if source[pos] == '{':
            depth += 1
        elif source[pos] == '}':
            depth -= 1
        pos += 1
    return source[start:pos] + '\n'


PREAMBLE = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <assert.h>
#include <sys/reboot.h>
#include <linux/reboot.h>
#include <linux/watchdog.h>
#define __iomem
#define CTRL 0
#define TCNT 8
#define RESET 12
#define SYS_DOWN 1
#define SYS_HALT 2
#define SYS_POWER_OFF 3
#define SYSTEM_RESTART 1
#define SYSTEM_HALT 2
#define SYSTEM_POWER_OFF 3
#define PM_SUSPEND_PREPARE 1
#define PM_POST_SUSPEND 2
#define NOTIFY_OK 1
#define dev_info(...) ((void)0)
#define mutex_lock(x) ((void)(x))
#define mutex_unlock(x) ((void)(x))
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
struct notifier_block { int unused; };
struct hrtimer { int unused; };
struct aml_wdt_dev {
 unsigned int default_timeout, shutdown_timeout, one_second, timeout;
 void *dev; int lock; bool hrtimer_running; void *reg_base;
 struct hrtimer timer; struct notifier_block pm_notifier, reboot_notifier;
 bool is_running;
};
struct watchdog_device { struct aml_wdt_dev *data; unsigned int timeout; };
struct platform_device { struct watchdog_device *data; };
/* Default-off trace boundaries; active readers/callers are exercised separately. */
static void aml_wdt_reboot_trace(const char *stage) { (void) stage; }
static struct aml_wdt_dev wdev;
static struct watchdog_device wdog;
static struct platform_device pdev;
static uint32_t regs[4];
static int system_state, cancels, resets, enables, disarms, magic, closes;
static bool disable_failure;
static char operations[64]; static unsigned int nops;
static void record(char c) { assert(nops+1<sizeof operations); operations[nops++]=c; }
static uint32_t readl(void *p) { return *(uint32_t *)p; }
static void writel(uint32_t v, void *p) {
 if (p==wdev.reg_base+RESET) { resets++; regs[2]&=0xffff; record('R'); }
 if (p==wdev.reg_base+TCNT) record('T');
 if (p==wdev.reg_base+CTRL && (v&(1u<<18))) { enables++; record('E'); }
 *(uint32_t *)p=v;
}
static void hrtimer_cancel(struct hrtimer *p) { (void)p; cancels++; }
static struct aml_wdt_dev *watchdog_get_drvdata(struct watchdog_device *p) { return p->data; }
static struct watchdog_device *platform_get_drvdata(struct platform_device *p) { return p->data; }
'''
USER = r'''
static void reboot_trace(const char *stage) { (void) stage; }
typedef uint64_t usec_t;
#define USEC_INFINITY UINT64_MAX
#define IN_SET(v,a,b) ((v)==(a)||(v)==(b))
static int watchdog_fd=-1;
static usec_t watchdog_timeout;
static char *watchdog_device;
static int open_result, update_result;
static int open_watchdog(void) { return open_result; }
static int update_timeout(void) { return update_result; }
static int free_and_strdup(char **p, const char *s) {
 if ((!*p&&!s)||(*p&&s&&!strcmp(*p,s))) return 0;
 char *new=s?strdup(s):NULL; free(*p); *p=new; return 1;
}
static int watchdog_set_enable(bool enabled) {
 if (enabled) return aml_wdt_start(&wdog);
 disarms++;
 return disable_failure ? -EIO : aml_wdt_stop(&wdog);
}
static ssize_t fake_write(int fd,const void *p,size_t n) {
 assert(fd==watchdog_fd && n==1 && *(const char *)p=='V'); magic++;
 /* Magic close independently requests driver stop when disable ioctl fails. */
 aml_wdt_stop(&wdog); return 1;
}
static int safe_close(int fd) { assert(fd==watchdog_fd); closes++; return -1; }
#define write fake_write
#define log_warning_errno(...) ((void)0)
void watchdog_close(bool disarm);
'''
MAIN = r'''
static void initialise(bool enabled) {
 free(watchdog_device); watchdog_device=strdup("/dev/watchdog0");
 memset(&wdev,0,sizeof wdev); memset(regs,0,sizeof regs);
 wdev.reg_base=regs; wdev.one_second=1000; wdev.shutdown_timeout=10;
 wdev.default_timeout=5; wdev.timeout=30; wdev.is_running=enabled;
 wdog.data=&wdev; wdog.timeout=30; pdev.data=&wdog;
 regs[0]=0xa3005dc0 | (enabled?(1u<<18):0); regs[2]=(1234u<<16)|30000;
 watchdog_fd=7; watchdog_timeout=30000000;
 system_state=SYSTEM_RESTART; cancels=resets=enables=disarms=magic=closes=0;
 disable_failure=false; open_result=0; update_result=0;
 nops=0; memset(operations,0,sizeof operations);
}
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"%s:%d: %s\n",name,__LINE__,#x); return 1; } } while(0)
static int run(const char *name) {
 initialise(true);
 if (!strcmp(name,"reboot-cleanup") || !strcmp(name,"kexec-cleanup")) {
  cleanup(!strcmp(name,"reboot-cleanup")?RB_AUTOBOOT:LINUX_REBOOT_CMD_KEXEC);
  CHECK(regs[0]&(1u<<18)); CHECK(!disarms&&!magic&&closes==1);
  CHECK(!watchdog_device && !watchdog_timeout && watchdog_fd==-1);
 } else if (!strcmp(name,"paired-handover")) {
  cleanup(RB_AUTOBOOT); CHECK(regs[0]&(1u<<18));
  aml_wtd_reboot_notify(&wdev.reboot_notifier,SYS_DOWN,NULL);
  CHECK(regs[0]&(1u<<18)); CHECK((regs[2]&0xffff)==10000); CHECK(regs[2]>>16==0);
 } else if (!strcmp(name,"stopped-reboot")) {
  aml_wdt_stop(&wdog); CHECK(!(regs[0]&(1u<<18)) && !wdev.is_running);
  nops=0; memset(operations,0,sizeof operations);
  aml_wtd_reboot_notify(&wdev.reboot_notifier,SYS_DOWN,NULL);
  CHECK(regs[0]&(1u<<18)); CHECK((regs[2]&0xffff)==10000);
  CHECK(!strcmp(operations,"TRE")); CHECK(!wdev.is_running);
 } else if (!strcmp(name,"halt-cleanup") || !strcmp(name,"poweroff-cleanup") || !strcmp(name,"exit-cleanup")) {
  int cmd=!strcmp(name,"halt-cleanup")?RB_HALT_SYSTEM:(!strcmp(name,"exit-cleanup")?0:RB_POWER_OFF);
  cleanup(cmd); CHECK(!(regs[0]&(1u<<18))); CHECK(disarms==1&&magic==1&&closes==1);
 } else if (!strcmp(name,"direct-free")) {
  watchdog_free_device(); CHECK(!watchdog_device); CHECK(disarms==1&&magic==1&&closes==1);
 } else if (!strcmp(name,"device-change")) {
  CHECK(watchdog_set_device("/dev/other")==1); CHECK(disarms==1&&magic==1&&closes==1);
 } else if (!strcmp(name,"same-device")) {
  CHECK(watchdog_set_device("/dev/watchdog0")==0); CHECK(!disarms&&!magic&&!closes);
 } else if (!strcmp(name,"timeout-zero")) {
  CHECK(watchdog_setup(0)==0); CHECK(disarms==1&&magic==1&&closes==1);
 } else if (!strcmp(name,"manager-error")) {
  manager_handover(0,true); CHECK(disarms==1&&magic==1&&closes==1);
 } else if (!strcmp(name,"manager-reboot")) {
  manager_handover(0,false); CHECK(!disarms&&!magic&&closes==1); CHECK(regs[0]&(1u<<18));
 } else if (!strcmp(name,"manager-halt") || !strcmp(name,"manager-poweroff")) {
  manager_handover(1,false); CHECK(disarms==1&&magic==1&&closes==1); CHECK(!(regs[0]&(1u<<18)));
 } else if (!strcmp(name,"open-error-cleanup")) {
  open_error_cleanup(); CHECK(disarms==1&&magic==1&&closes==1);
 } else if (!strcmp(name,"disable-error")) {
  disable_failure=true; watchdog_close(true); CHECK(disarms==1&&magic==1&&closes==1);
 } else if (!strcmp(name,"repeated-cleanup")) {
  cleanup(RB_AUTOBOOT); cleanup(RB_AUTOBOOT); CHECK(!disarms&&!magic&&closes==1);
 } else if (!strcmp(name,"unopened")) {
  watchdog_fd=-1; cleanup(RB_AUTOBOOT); CHECK(!disarms&&!magic&&!closes);
 } else if (!strcmp(name,"disabled-cleanup")) {
  aml_wdt_stop(&wdog); cleanup(RB_AUTOBOOT); CHECK(!(regs[0]&(1u<<18))); CHECK(!enables);
 } else if (!strcmp(name,"reboot-registers")) {
  uint32_t ctrl=regs[0]; wdev.hrtimer_running=true;
  aml_wtd_reboot_notify(&wdev.reboot_notifier,SYS_DOWN,NULL);
  CHECK(regs[0]==ctrl); CHECK((regs[2]&0xffff)==10000); CHECK(regs[2]>>16==0);
  CHECK(cancels==1&&!wdev.hrtimer_running); CHECK(resets==1);
  aml_wtd_reboot_notify(&wdev.reboot_notifier,SYS_DOWN,NULL); CHECK(cancels==1);
 } else if (!strcmp(name,"kernel-halt")) {
  wdev.hrtimer_running=true;
  aml_wtd_reboot_notify(&wdev.reboot_notifier,SYS_HALT,NULL);
  CHECK(!(regs[0]&(1u<<18))); CHECK(!resets&&!enables&&cancels==1);
  system_state=SYSTEM_HALT; aml_wdt_shutdown(&pdev); CHECK(!(regs[0]&(1u<<18)));
 } else if (!strcmp(name,"kernel-poweroff")) {
  aml_wtd_reboot_notify(&wdev.reboot_notifier,SYS_POWER_OFF,NULL);
  CHECK(!resets&&!enables); system_state=SYSTEM_POWER_OFF;
  aml_wdt_shutdown(&pdev); CHECK(!(regs[0]&(1u<<18)));
 } else if (!strcmp(name,"kernel-restart-shutdown")) {
  uint32_t ctrl=regs[0],cnt=regs[2]; aml_wdt_shutdown(&pdev);
  CHECK(regs[0]==ctrl&&regs[2]==cnt&&!resets&&!enables);
 } else if (!strcmp(name,"unknown-event")) {
  aml_wdt_stop(&wdog); aml_wtd_reboot_notify(&wdev.reboot_notifier,99,NULL);
  CHECK(!(regs[0]&(1u<<18))); CHECK(!resets&&!enables);
 } else if (!strcmp(name,"runtime-stop")) {
  aml_wdt_stop(&wdog); CHECK(!(regs[0]&(1u<<18))&&!wdev.is_running);
  CHECK(!enables&&!resets);
 } else if (!strcmp(name,"pm-running")) {
  aml_wtd_pm_notify(&wdev.pm_notifier,PM_SUSPEND_PREPARE,NULL);
  CHECK(!(regs[0]&(1u<<18))&&wdev.is_running);
  aml_wtd_pm_notify(&wdev.pm_notifier,PM_POST_SUSPEND,NULL); CHECK(regs[0]&(1u<<18));
 } else if (!strcmp(name,"pm-stopped")) {
  aml_wdt_stop(&wdog); aml_wtd_pm_notify(&wdev.pm_notifier,PM_SUSPEND_PREPARE,NULL);
  aml_wtd_pm_notify(&wdev.pm_notifier,PM_POST_SUSPEND,NULL); CHECK(!(regs[0]&(1u<<18))&&!enables);
 } else { CHECK(false); }
 return 0;
}
int main(int argc,char **argv) { assert(argc==2); int r=run(argv[1]); free(watchdog_device); return r; }
'''
CASES = ['reboot-cleanup','kexec-cleanup','paired-handover','stopped-reboot',
         'halt-cleanup','poweroff-cleanup','exit-cleanup','direct-free','device-change','same-device',
         'timeout-zero','manager-error','manager-reboot','manager-halt','manager-poweroff','open-error-cleanup',
         'disable-error','repeated-cleanup','unopened','disabled-cleanup','reboot-registers','kernel-halt',
         'kernel-poweroff','kernel-restart-shutdown','unknown-event','runtime-stop','pm-running','pm-stopped']


def harness(shutdown, watchdog, header, manager, kernel):
    # Use actual production functions and caller statements; only OS/MMIO boundaries are fakes.
    result = PREAMBLE
    for name in ['aml_update_bits','enable_watchdog','disable_watchdog','reset_watchdog',
                 'set_watchdog_cnt','aml_wdt_start','aml_wdt_stop','aml_wtd_pm_notify',
                 'aml_wtd_reboot_notify','aml_wdt_shutdown']:
        result += function(kernel, name)
    result += USER
    for name in ['watchdog_close','watchdog_set_device','watchdog_setup']:
        result += function(watchdog, name)
    result += function(header, 'watchdog_free_device')
    start = shutdown.index('        /* We\'re done with the watchdog.')
    end = shutdown.index('        arguments[0]', start)
    result += 'static void cleanup(int cmd) {\n' + shutdown[start:end] + '}\n'
    # Exact manager policy and error-dependent close, before environment construction.
    start = manager.index('        if (objective == MANAGER_REBOOT)')
    end = manager.index('        /* The environment block: */', start)
    result += '''\n#define MANAGER_REBOOT 0
#define MANAGER_KEXEC 2
#define watchdog_setup_pretimeout(x) 0
#define watchdog_setup_pretimeout_governor(x) 0
static void manager_handover(int objective,bool error) {
 usec_t watchdog_timer=0,arg_reboot_watchdog=30000000,arg_kexec_watchdog=30000000;
 int r; update_result=error?-EIO:0;
 if (error) watchdog_timeout=29000000; /* Force real reconfiguration, not duplicate-request shortcut. */
'''+manager[start:end]+'}\n'
    # Assert the actual failure target, without opening any host device.
    opened = function(watchdog, 'open_watchdog')
    result += 'static void open_error_cleanup(void) {\n'+opened[opened.index('close_and_fail:')+len('close_and_fail:'):opened.rindex('return r;')]+'}\n'
    return result + MAIN


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--systemd-tree',type=pathlib.Path,required=True)
    parser.add_argument('--kernel-tree',type=pathlib.Path,required=True)
    parser.add_argument('--cc',default='cc')
    args=parser.parse_args()
    tree=args.systemd_tree
    shutdown=(tree/'src/shutdown/shutdown.c').read_text()
    watchdog=(tree/'src/shared/watchdog.c').read_text()
    header=(tree/'src/shared/watchdog.h').read_text()
    manager=(tree/'src/core/main.c').read_text()
    kernel=(args.kernel_tree/'drivers/amlogic/watchdog/meson_wdt.c').read_text()
    # Recover old callbacks from the fixed source, or use a baseline input directly.
    rearm='\t\t/* Reboot owns the guard even if userspace previously stopped it. */\n\t\tenable_watchdog(wdev);\n'
    old_kernel=kernel.replace(rearm,'')
    assert rearm in kernel, 'kernel input must contain focused correction'
    fixed_cleanup='        if (IN_SET(cmd, RB_AUTOBOOT, LINUX_REBOOT_CMD_KEXEC))\n                watchdog_close(/* disarm= */ false);\n'
    old_shutdown=shutdown.replace(fixed_cleanup,'')
    with tempfile.TemporaryDirectory(prefix='watchdog-handover-') as tmp:
        temp=pathlib.Path(tmp)
        # Synthetic old/one-sided fixtures remove only the production correction.
        # The paired fixture is the exact final input, including diagnostic callers.
        new_shutdown=shutdown
        assert new_shutdown.count(fixed_cleanup)==1
        assert 'static void reboot_trace(' in new_shutdown, 'Use final package-prepared source'
        for label,sd,k,expected in [
            ('old',old_shutdown,old_kernel,{'reboot-cleanup','kexec-cleanup','paired-handover','stopped-reboot','repeated-cleanup'}),
            ('userspace-only',new_shutdown,old_kernel,{'stopped-reboot'}),
            ('kernel-only',old_shutdown,kernel,{'reboot-cleanup','kexec-cleanup','paired-handover','repeated-cleanup'}),
            ('paired',new_shutdown,kernel,set())]:
            src=temp/(label+'.c'); binary=temp/label
            src.write_text(harness(sd,watchdog,header,manager,k))
            subprocess.run([args.cc,'-std=gnu11','-Wall','-Wextra','-Werror','-Wno-unused-parameter',str(src),'-o',str(binary)],check=True)
            failed=set()
            for case in CASES:
                run=subprocess.run([str(binary),case],text=True,capture_output=True)
                if run.returncode:
                    assert run.returncode==1, (label,case,run.stderr)
                    failed.add(case)
                    print(f'{label}: {run.stderr.strip()}')
            assert failed==expected,(label,failed,expected)
            print(f'{label}: {len(CASES)-len(failed)}/{len(CASES)} pass; expected protection failures={sorted(failed)}')
    print('PASS: original coverage failure reproduced; paired fix and all control paths verified (host boundaries simulated).')


if __name__=='__main__':
    main()
