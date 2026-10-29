#!/usr/bin/env python3
"""Exercise production readiness and SDIO ownership code without a loaded module.

Usage: python3 check_startup.py /path/to/unpacked-and-patched-package
Kernel locking, actual sysfs linkage and device startup still need image tests.
"""

from pathlib import Path
import re
import sys

from check_source import function, run_c


def readiness(linux):
    opening = function(linux, 'dhd_open')
    start = opening.index('\tif (!dhd_download_fw_on_driverload) {')
    end = opening.index('\n\tprintf("%s: Enter %s', start)
    guard = opening[start:end]
    assert end < opening.index('\tDHD_MUTEX_LOCK();')
    declarations = '\n'.join(re.findall(
        r'^\t(?:int retry = POWERUP_MAX_RETRY|unsigned long init_deadline);$', opening, re.M))
    assert 'retry = POWERUP_MAX_RETRY' in declarations
    publisher = function(linux, 'dhd_module_init')
    assert publisher.count('WRITE_ONCE(dhd_driver_init_done, TRUE);') == 1
    assert publisher.index('WRITE_ONCE(dhd_driver_init_done, TRUE);') > publisher.index(
        'dhd_create_to_notifier_skt();')
    run_c('first-open readiness, timeout and power-up retry budget', r'''
#include <assert.h>
#define POWERUP_MAX_RETRY 3
#define READ_ONCE(value) (value)
#define msecs_to_jiffies(ms) (((ms) + 3) / 4)
#define time_after_eq(a,b) ((long)((a) - (b)) >= 0)
static unsigned long jiffies;
#define DHD_ERROR(args) ((void)0)
static int dhd_download_fw_on_driverload, dhd_driver_init_done;
static int elapsed, ready_after, unlocks;
static struct { struct { int ndev_op_sync; } pub; } device;
#define mutex_unlock(lock) ((void)(lock), ++unlocks)
static void OSL_SLEEP(int milliseconds) {
    assert(milliseconds > 0 && milliseconds <= 20);
    /* Model HZ=250 and msleep rounding up by one scheduler tick. */
    int ticks = msecs_to_jiffies(milliseconds) + 1;
    jiffies += ticks;
    elapsed += ticks * 4;
    assert(elapsed <= 3000); /* Also bounds a mistakenly endless polling loop. */
    if (ready_after >= 0 && elapsed >= ready_after)
        dhd_driver_init_done = 1;
}
static int first_open(void) {
    __typeof__(device) *dhd = &device;
''' + declarations + '\n' + guard + r'''
    return retry;
}
static void check(int initial, int after, int preload, int result,
                  int expected_elapsed, int expected_unlocks) {
    dhd_driver_init_done = initial;
    ready_after = after;
    dhd_download_fw_on_driverload = preload;
    elapsed = unlocks = 0;
    assert(first_open() == result);
    assert(elapsed == expected_elapsed);
    assert(unlocks == expected_unlocks);
}
int main(void) {
    (void)OSL_SLEEP;
    check(1, -1, 0, POWERUP_MAX_RETRY, 0, 0);
    check(0, 1, 0, POWERUP_MAX_RETRY, 24, 0);
    check(0, 200, 0, POWERUP_MAX_RETRY, 216, 0);
    jiffies = (unsigned long)-100;
    check(0, 200, 0, POWERUP_MAX_RETRY, 216, 0);
    check(0, 2999, 0, POWERUP_MAX_RETRY, 3000, 0);
    check(0, 3000, 0, POWERUP_MAX_RETRY, 3000, 0);
    check(0, 3001, 0, -1, 3000, 1);
    check(0, -1, 0, -1, 3000, 1);
    check(0, -1, 1, POWERUP_MAX_RETRY, 0, 0);
    return 0;
}
''')


def ownership(sdio):
    initializers = []
    for name in ('dummy_sdmmc_driver', 'bcmsdh_sdmmc_driver'):
        match = re.search(r'^static struct sdio_driver ' + name +
                          r' = \{.*?^\s*\};', sdio, re.M | re.S)
        assert match, name
        initializers.append(match.group())
    for pm in (False, True):
        run_c('bound SDIO module ownership with PM ' + ('on' if pm else 'off'), r'''
#include <assert.h>
#define KERNEL_VERSION(a,b,c) (((a) << 16) | ((b) << 8) | (c))
#define LINUX_VERSION_CODE KERNEL_VERSION(4,9,269)
''' + ('#define CONFIG_PM 1\n' if pm else '') + r'''
static int owner, callback;
#ifdef CONFIG_PM
static int bcmsdh_sdmmc_pm_ops;
#endif
#define THIS_MODULE (&owner)
#define dummy_probe (&callback)
#define dummy_remove (&callback)
#define bcmsdh_sdmmc_probe (&callback)
#define bcmsdh_sdmmc_remove (&callback)
#define bcmsdh_sdmmc_ids (&callback)
struct device_driver { void *owner; void *pm; };
struct sdio_driver {
    void *probe, *remove;
    const char *name;
    void *id_table;
    struct device_driver drv;
};
''' + '\n'.join(initializers) + r'''
int main(void) {
    assert(dummy_sdmmc_driver.drv.owner == THIS_MODULE);
    assert(bcmsdh_sdmmc_driver.drv.owner == THIS_MODULE);
    assert(dummy_sdmmc_driver.drv.pm == 0);
#ifdef CONFIG_PM
    assert(bcmsdh_sdmmc_driver.drv.pm == &bcmsdh_sdmmc_pm_ops);
#else
    assert(bcmsdh_sdmmc_driver.drv.pm == 0);
#endif
    assert(bcmsdh_sdmmc_driver.probe == bcmsdh_sdmmc_probe);
    assert(bcmsdh_sdmmc_driver.remove == bcmsdh_sdmmc_remove);
    assert(bcmsdh_sdmmc_driver.id_table == bcmsdh_sdmmc_ids);
    return 0;
}
''')


def main():
    source = Path(sys.argv[1]) / 'bcmdhd.100.10.545.x'
    readiness((source / 'dhd_linux.c').read_text())
    ownership((source / 'bcmsdh_sdmmc_linux.c').read_text())


if __name__ == '__main__':
    main()
