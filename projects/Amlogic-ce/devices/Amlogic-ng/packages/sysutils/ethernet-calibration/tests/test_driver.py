# SPDX-License-Identifier: GPL-2.0-or-later
"""Host regressions executing the patched driver functions with deterministic stubs."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SOURCE = os.environ.get('CE_CALIBRATION_KERNEL_SOURCE')
REFERENCE = os.environ.get('CE_CALIBRATION_REFERENCE_SOURCE')


def function(text, signature):
    start = text.index(signature)
    opening = text.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


WATCHDOG = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>
#include <sys/types.h>
#define HZ 100UL
#define EINVAL 22
#define EBUSY 16
#define time_before(a,b) ((long)((a)-(b)) < 0)
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define to_delayed_work(p) container_of(p,struct delayed_work,work)
#define dev_warn(d,f,...) printf(f,__VA_ARGS__)
struct work_struct { int unused; };
struct delayed_work { struct work_struct work; };
struct meson_dwmac {
    struct delayed_work calibration_timeout;
    bool calibration_lease, calibration_applied, calibration_suspending;
    unsigned long calibration_expires;
    int calibration_index, calibration_accepted;
    void *dev;
};
struct platform { void *bsp_priv; };
struct stmmac_priv { struct platform *plat; };
struct net_device { struct stmmac_priv *priv; };
struct device { struct net_device *ndev; };
struct device_attribute { int unused; };
static struct meson_dwmac dw;
static struct platform plat = { &dw };
static struct stmmac_priv priv = { &plat };
static struct net_device ndev = { &priv };
static struct device device = { &ndev };
static void *system_wq;
static unsigned long jiffies, remaining;
static int restores, rearmed, depth;
static void (*lock_hook)(void);
static void rtnl_lock(void) {
    if (lock_hook) {
        void (*hook)(void) = lock_hook;
        lock_hook = NULL;
        hook();
    }
    assert(depth++ == 0);
}
static void rtnl_unlock(void) { assert(--depth == 0); }
static struct net_device *to_net_dev(struct device *d) { return d->ndev; }
static struct stmmac_priv *netdev_priv(struct net_device *d) { return d->priv; }
static int sysfs_streq(const char *a, const char *b) { return !strcmp(a,b); }
static int kstrtoint(const char *s, int base, int *value) {
    char *end;
    *value = strtol(s, &end, base);
    return *end ? -EINVAL : 0;
}
static int meson_calibration_switch(struct meson_dwmac *d, int index) {
    assert(depth == 1);
    d->calibration_index = index;
    d->calibration_applied = true;
    return 0;
}
static int meson_calibration_restore(struct meson_dwmac *d) {
    restores++;
    return meson_calibration_switch(d, d->calibration_accepted);
}
static void mod_delayed_work(void *w, struct delayed_work *d, unsigned long delay) {
    assert(d == &dw.calibration_timeout);
    rearmed++;
    remaining = delay;
}
static void cancel_delayed_work(struct delayed_work *d) {}
'''

WATCHDOG_CASES = r'''
static void command(const char *s) {
    assert(ce_calibration_store(&device, NULL, s, strlen(s)) == (ssize_t)strlen(s));
}
static void renewal(void) { jiffies++; command("keepalive"); }
static void rebegin(void) {
    command("restore");
    command("begin");
    command("set 7");
}
int main(int argc, char **argv) {
    assert(argc == 2);
    dw.calibration_index = dw.calibration_accepted = 30;
    dw.calibration_applied = true;
    jiffies = !strcmp(argv[1], "wrap") ? ULONG_MAX - 10 * HZ : 100;
    command("begin");
    command("set 3");
    jiffies += 10 * HZ;
    rearmed = 0;
    if (!strcmp(argv[1], "rebegin")) lock_hook = rebegin;
    else if (strcmp(argv[1], "expiry")) lock_hook = renewal;
    meson_calibration_expire(&dw.calibration_timeout.work);
    if (!strcmp(argv[1], "expiry")) {
        assert(restores == 1 && !dw.calibration_lease);
        assert(dw.calibration_index == 30);
    } else {
        assert(dw.calibration_lease && remaining == 10 * HZ);
        assert(dw.calibration_index == (!strcmp(argv[1], "rebegin") ? 7 : 3));
        assert(restores == (!strcmp(argv[1], "rebegin") ? 1 : 0));
        assert(rearmed > 0);
    }
    assert(depth == 0);
    return 0;
}
'''

RESUME = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#define CONFIG_AMLOGIC_ETH_PRIVE 1
#define MAXIO_PHY_MAE0621A_ID 0x7b744411
#define spin_lock_irqsave(lock,flags) ((void)(flags = 0))
#define spin_unlock_irqrestore(lock,flags) ((void)flags)
struct device { int unused; };
struct phy_device;
struct phy_driver { int (*config_init)(struct phy_device *); };
struct phy_device { unsigned phy_id; struct phy_driver *drv; };
struct mac_ops { void (*pmt)(void *,int); };
struct hardware { struct mac_ops *mac; };
struct platform { int (*calibration_init)(void *,struct phy_device *); void *bsp_priv; };
struct net_device { bool up, attached; struct phy_device *phydev; };
struct stmmac_priv {
    struct device *device;
    struct hardware *hw;
    struct platform *plat;
    struct net_device *dev;
    struct phy_device *phydev;
    int lock, irq_wake, stmmac_clk, pclk, mii, napi;
    int cur_rx, dirty_rx, dirty_tx, cur_tx, mss;
};
static struct net_device ndev;
static struct stmmac_priv priv;
static int disabled = 1, doubled, mode, in_open, depth, closes;
static int apply(void *unused, struct phy_device *phy) {
    return mode == 1 || (mode == 2 && in_open) ? -5 : 0;
}
static void pmt(void *h,int v) {}
static void *dev_get_drvdata(struct device *d) { return &ndev; }
static struct stmmac_priv *netdev_priv(struct net_device *d) { return &priv; }
static bool netif_running(struct net_device *d) { return d->up; }
static bool device_may_wakeup(struct device *d) { return true; }
static void pinctrl_pm_select_default_state(struct device *d) {}
static void clk_enable(int c) {}
static void stmmac_mdio_reset(int m) {}
static void stmmac_clear_descriptors(struct stmmac_priv *p) {}
static void netdev_reset_queue(struct net_device *d) {}
static void stmmac_hw_setup(struct net_device *d,bool v) {}
static void stmmac_init_tx_coalesce(struct stmmac_priv *p) {}
static void stmmac_set_rx_mode(struct net_device *d) {}
static void netif_device_attach(struct net_device *d) { d->attached = true; }
static void netif_start_queue(struct net_device *d) {}
static void phy_start(struct phy_device *p) {}
static void napi_enable(int *n) { assert(disabled); disabled = 0; }
static int stmmac_release(struct net_device *d) {
    if (disabled) doubled++;
    disabled = 1;
    return 0;
}
static int stmmac_open(struct net_device *d) {
    in_open = 1;
    int ret = priv.plat->calibration_init ?
        priv.plat->calibration_init(NULL, d->phydev) : 0;
    in_open = 0;
    if (!ret) napi_enable(&priv.napi);
    return ret;
}
static void rtnl_lock(void) { assert(depth++ == 0); }
static void rtnl_unlock(void) { assert(--depth == 0); }
static void dev_close(struct net_device *d) {
    closes++;
    if (d->up) { stmmac_release(d); d->up = false; }
}
static int dev_open(struct net_device *d) {
    if (d->up) return 0;
    int ret = stmmac_open(d);
    if (!ret) d->up = true;
    return ret;
}
'''

RESUME_CASES = r'''
int main(int argc,char **argv) {
    struct device device;
    struct phy_driver driver = { NULL };
    struct phy_device phy = { 0x001cc916, &driver };
    struct mac_ops mac = { pmt };
    struct hardware hw = { &mac };
    struct platform plat = { apply, NULL };
    assert(argc == 2);
    mode = !strcmp(argv[1], "early") ? 1 : !strcmp(argv[1], "open") ? 2 :
           !strcmp(argv[1], "unsupported") ? 3 : 0;
    if (mode == 3) plat.calibration_init = NULL;
    ndev.up = true; ndev.phydev = &phy;
    priv.dev = &ndev; priv.device = &device; priv.hw = &hw;
    priv.plat = &plat; priv.phydev = &phy;
    int ret = stmmac_resume(&device);
    if (mode == 1 || mode == 2) {
        assert(ret == -5 && !ndev.up && disabled);
        dev_close(&ndev);
        assert(!doubled);
    } else {
        assert(!ret && ndev.up && ndev.attached && !disabled && !doubled);
        if (mode == 3) assert(!closes);
    }
    assert(depth == 0);
    return 0;
}
'''


@unittest.skipUnless(SOURCE and shutil.which('cc'), 'Requires prepared kernel source and host cc')
class DriverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temporary.name)
        cls.binaries = {}
        cls.hashes = {}
        for label, source in (('candidate', SOURCE), ('reference', REFERENCE)):
            if source is None:
                continue
            root = Path(source) / 'drivers/net/ethernet/stmicro/stmmac'
            meson, main = (root / 'dwmac-meson.c').read_text(), (root / 'stmmac_main.c').read_text()
            programs = {
                'watchdog': WATCHDOG + function(meson, 'static void meson_calibration_expire(') +
                            function(meson, 'static ssize_t ce_calibration_store(') + WATCHDOG_CASES,
                'resume': RESUME + function(main, 'int stmmac_resume(') + RESUME_CASES,
            }
            for name, text in programs.items():
                path = cls.directory / (label + '-' + name + '.c')
                path.write_text(text)
                binary = path.with_suffix('')
                subprocess.run(['cc', '-std=gnu11', '-Werror=implicit-function-declaration',
                                str(path), '-o', str(binary)], check=True, capture_output=True)
                cls.binaries[label, name] = binary
                cls.hashes[label + '-' + name] = hashlib.sha256(text.encode()).hexdigest()

    @classmethod
    def tearDownClass(cls):
        output = os.environ.get('CE_CALIBRATION_DRIVER_RECEIPT')
        if output:
            Path(output).write_text(json.dumps({
                'source': SOURCE, 'reference': REFERENCE, 'program_sha256': cls.hashes,
                'scope': 'Actual C functions; host stubs simulate RTNL interleaving and NAPI/admin flags. Not hardware proof.',
            }, indent=2))
        cls.temporary.cleanup()

    def check(self, group, scenario, reference_passes=False):
        def no_core():
            import resource
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        result = subprocess.run([str(self.binaries['candidate', group]), scenario],
                                capture_output=True, preexec_fn=no_core)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        if REFERENCE:
            old = subprocess.run([str(self.binaries['reference', group]), scenario],
                                 capture_output=True, preexec_fn=no_core)
            self.assertEqual(old.returncode == 0, reference_passes,
                             'Negative control did not distinguish previous driver')

    def test_entered_watchdog_does_not_expire_renewed_lease(self):
        self.check('watchdog', 'renew')

    def test_entered_watchdog_does_not_expire_rebegun_lease(self):
        self.check('watchdog', 'rebegin')

    def test_watchdog_really_expired_lease_restores_original(self):
        self.check('watchdog', 'expiry', True)

    def test_watchdog_renewal_remains_valid_across_jiffies_wrap(self):
        self.check('watchdog', 'wrap')

    def test_resume_apply_failure_does_not_double_disable_napi(self):
        self.check('resume', 'early')

    def test_resume_reopen_failure_is_returned_and_interface_is_down(self):
        self.check('resume', 'open')

    def test_successful_resume_balances_napi_and_admin_state(self):
        self.check('resume', 'success', True)

    def test_unsupported_resume_preserves_vendor_reopen_path(self):
        self.check('resume', 'unsupported', True)


if __name__ == '__main__':
    unittest.main()
