#!/usr/bin/env python3
"""Run bounded host checks against an unpacked, patched driver source tree.

Usage: python3 check_source.py /path/to/ap6275s-aml-<revision>
These exercise extracted production code, not firmware or a loaded kernel module.
"""

import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile


def function(text, name):
    match = re.search(r"^\s*" + re.escape(name) + r"\s*\([^;{}]*\)\s*\{", text, re.M)
    assert match, name
    start = match.start()
    opening = match.end() - 1
    depth = 1
    end = opening + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def run_c(label, code):
    with tempfile.TemporaryDirectory(prefix="ap6275s-check-") as temporary:
        source = Path(temporary) / "check.c"
        binary = Path(temporary) / "check"
        source.write_text(code)
        subprocess.run(
            [*shlex.split(os.environ.get("CC", "cc")), "-std=gnu11", "-Wall",
             "-Wextra", "-Werror", str(source), "-o", str(binary)], check=True
        )
        subprocess.run([str(binary)], check=True)
    print(label + ": PASS")


def profiles(source, config):
    helper = function(config, 'dhd_conf_set_tput_patch')
    override = function(config, 'dhd_conf_set_p3i_tput_profile')
    sleep = function(config, 'dhd_conf_disable_slpauto')
    wiring = function(config, 'dhd_conf_set_path_params')
    start = config.rindex('\tif (conf->chip == BCM43752_CHIP_ID || conf->chip == BCM4375_CHIP_ID ||')
    profile = function(config[start:], 'if')
    initial = re.search(r'\tconf->tput_patch = FALSE;\n\tconf->mtu = 0;\n\tconf->pktsetsum = FALSE;', config).group()
    start = config.index('else if (!strncmp("tput_patch=",')
    parser = config[start:config.index('\n\t}', start) + len('\n\t}')]
    header = '\n'.join(path.read_text() for path in (source / 'include').glob('bcmdevs*.h'))
    constants = []
    for name in sorted(set(re.findall(r'BCM\w+_CHIP_ID', profile))):
        value = re.search(r'^#define\s+' + name + r'\s+(0x[0-9a-fA-F]+|[0-9]+)', header, re.M)[1]
        constants.append('#define ' + name + ' ' + value)
    cpus = re.search(r'#if defined\(DHD_TPUT_PATCH\)\n#define RPS_CPUS_MASK "([^"]+)"', (source / 'dhd.h').read_text())[1]
    assert re.search(r'^#define CONFIG_PATH_AUTO_SELECT\s*$', (source / 'dhd_config.h').read_text(), re.M)
    assert wiring.index('dhd_conf_read_config') < wiring.index('dhd_conf_set_p3i_tput_profile')
    run_c('fresh defaults, board configuration, safe checksums and profile ownership', r'''
#include <assert.h>
#include <stdlib.h>
#include <string.h>
#define TRUE 1
#define FALSE 0
#define BCMSDIO 1
#define DHD_TPUT_PATCH 1
#define DYNAMIC_MAX_HDR_READ 1
#define DHDTCPACK_SUPPRESS 1
#define SET_XPS_CPUS 1
#define SET_RPS_CPUS 1
#define GFP_KERNEL 0
#define CUSTOM_TCPACK_SUPP_RATIO 3
#define CUSTOM_TCPACK_DELAY_TIME 10
#define CONFIG_MSG(...) ((void)0)
#define CONFIG_ERROR(...) ((void)0)
''' + '\n'.join(constants) + '\n#define RPS_CPUS_MASK "' + cpus + '"\n' + r'''
struct dhd_conf {
    int chip, chiprev, tput_patch, mtu, pktsetsum, dhd_dpc_prio, disable_proptx;
    int frameburst, max_hdr_read, tcpack_sup_ratio, tcpack_sup_delay;
    char *xps_cpus, *rps_cpus;
};
struct dhd_info { char fw_path[128], nv_path[128], clm_path[128], conf_path[128]; };
typedef struct { struct dhd_conf *conf; struct dhd_info *info; } dhd_pub_t;
static int firstread, dhd_rxbound, dhd_slpauto, live_allocations, fail_alloc;
static int p3i_tput_profile = -1, p3i_tput_active = -1, manual_profile = -1;
static void *kmalloc(size_t bytes, int flags) {
    (void)flags;
    if (fail_alloc) return NULL;
    void *value = malloc(bytes);
    assert(value);
    ++live_allocations;
    return value;
}
static void kfree(void *value) { assert(value); --live_allocations; free(value); }
void
''' + helper + '\nstatic void\n' + override + '\nstatic void\n' + sleep + r'''
static void dhd_conf_preinit(dhd_pub_t *dhd) {
    struct dhd_conf *conf = dhd->conf;
''' + initial + '\n' + profile + r'''
    dhd_conf_disable_slpauto(dhd);
}
static void configure(dhd_pub_t *dhd, const char *full_param, const char *data) {
    struct dhd_conf *conf = dhd->conf;
    int len_param = 11;
    if (0) {}
''' + parser + r'''
}
static void dhd_conf_read_config(dhd_pub_t *dhd, const char *path) {
    assert(!strcmp(path, "/storage/custom/config_bcm43752a2_ag.txt"));
    if (manual_profile >= 0)
        configure(dhd, "tput_patch=", manual_profile ? "1" : "0");
}
static void dhd_conf_dump_tput_patch(dhd_pub_t *dhd) { (void)dhd; }
void
''' + wiring + r'''
int main(void) {
    struct dhd_conf conf = {.chip = BCM43752_CHIP_ID, .chiprev = 2};
    struct dhd_info info = {.conf_path = "/storage/custom/config_bcm43752a2_ag.txt"};
    dhd_pub_t d = {.conf = &conf, .info = &info};
    dhd_slpauto = 1;
    dhd_conf_preinit(&d);
    assert(!conf.tput_patch && !conf.pktsetsum && dhd_slpauto == 0);
    assert(conf.dhd_dpc_prio == -1 && conf.disable_proptx == -1);
    assert(firstread == 32 && dhd_rxbound == 128 && conf.max_hdr_read == 0);
    configure(&d, "tput_patch=", "1");
    assert(conf.tput_patch && !conf.pktsetsum && conf.dhd_dpc_prio == 98);
    assert(firstread == 256 && dhd_rxbound == 512);
    assert(live_allocations == 2 && !strcmp(conf.rps_cpus, RPS_CPUS_MASK));
    configure(&d, "tput_patch=", "0");
    assert(!conf.tput_patch && !conf.pktsetsum && conf.dhd_dpc_prio == -1);
    assert(!conf.rps_cpus && !conf.xps_cpus && live_allocations == 0);
    dhd_conf_set_p3i_tput_profile(&d);
    assert(p3i_tput_active == 0);
    manual_profile = 1;
    dhd_conf_set_path_params(&d);
    assert(p3i_tput_active == 1 && conf.tput_patch && !conf.pktsetsum);
    p3i_tput_profile = 0;
    dhd_conf_set_path_params(&d);
    assert(p3i_tput_active == 0 && !conf.tput_patch && !conf.pktsetsum);
    assert(!conf.rps_cpus && !conf.xps_cpus && live_allocations == 0);
    p3i_tput_profile = 1;
    dhd_conf_set_path_params(&d);
    assert(p3i_tput_active == 1 && conf.tput_patch && !conf.pktsetsum);
    assert(live_allocations == 2);
    dhd_conf_set_p3i_tput_profile(&d);
    assert(live_allocations == 2); /* Repeated application replaces, not leaks. */
    conf.chiprev = 1;
    dhd_conf_set_p3i_tput_profile(&d);
    assert(p3i_tput_active == -1);
    conf.chiprev = 2;
    conf.chip = 0x4354;
    dhd_conf_set_p3i_tput_profile(&d);
    assert(p3i_tput_active == -1);
    dhd_slpauto = 1;
    dhd_conf_disable_slpauto(&d);
    assert(dhd_slpauto == 0); /* Manual zero is effective on every supported ID. */
    conf.chip = BCM43752_CHIP_ID;
    p3i_tput_profile = 0;
    dhd_conf_set_p3i_tput_profile(&d);
    assert(!conf.tput_patch && live_allocations == 0);
    p3i_tput_profile = 1;
    fail_alloc = 1;
    dhd_conf_set_p3i_tput_profile(&d);
    assert(conf.tput_patch && !conf.pktsetsum && live_allocations == 0);
    fail_alloc = 0;
    manual_profile = -1;
    p3i_tput_profile = -1;
    dhd_conf_set_path_params(&d);
    assert(p3i_tput_active == 0 && !conf.tput_patch && !conf.pktsetsum);
    return 0;
}
''')


def main():
    source = Path(sys.argv[1]) / "bcmdhd.101.10.591.x"
    android = (source / "wl_android.c").read_text()
    cfg = (source / "wl_cfg80211.c").read_text()
    config = (source / "dhd_config.c").read_text()
    start = android.index("\t\tchar *country_code = command + strlen(CMD_COUNTRY)")
    end = android.index("\t\tbytes_written = wl_cfg80211_set_country_code", start)
    country = android[start:end]
    run_c("country revision parsing", r'''
#include <assert.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#define CMD_COUNTRY "COUNTRY"
#define CMD_COUNTRY_DELIMITER "/"
#define strnicmp strncasecmp
#define bcm_atoi atoi
#define DHD_ERROR(args) ((void)0)
#define BCME_BADARG -2
static int parse(char *command) {
''' + country + r'''
    return revinfo;
}
int main(void) {
    assert(parse("COUNTRY US") == 0);
    assert(parse("COUNTRY US/-1") == BCME_BADARG);
    assert(parse("COUNTRY US/7") == 7);
    assert(parse("COUNTRY DE/0") == 0);
    assert(parse("COUNTRY US/123") == 123);
    assert(parse("COUNTRY US/") == 0);
    assert(parse("COUNTRY US:7") == 0);
    return 0;
}
''')
    gtk = function(cfg, "wl_cfg80211_set_rekey_data")
    run_c("GTK key transfer and error paths without key logging", r'''
#include <assert.h>
#include <errno.h>
#include <string.h>
typedef int s32;
typedef unsigned char u8;
#define RSN_KCK_LENGTH 16
#define RSN_KEK_LENGTH 16
#define RSN_REPLAY_LEN 8
#define WLC_IOCTL_SMLEN 256
#define WL_DBG(x) ((void)0)
#define WL_ERR(x) ((void)0)
#define WL_TRACE(x) ((void)0)
#define BCME_UNSUPPORTED -23
#define prhex(...) (++logged)
#define bcopy(a,b,n) memcpy(b,a,n)
struct net_device { int unused; };
struct cfg80211_gtk_rekey_data { u8 kck[16], kek[16], replay_ctr[8]; };
typedef struct { u8 KCK[16], KEK[16], ReplayCounter[8]; } gtk_keyinfo_t;
typedef struct { int enable, ptk_len; u8 ptk[64]; } bcol_gtk_para_t;
struct dhd_conf { int rekey_offload; };
typedef struct { struct dhd_conf *conf; } dhd_pub_t;
struct bcm_cfg80211 { dhd_pub_t *pub; struct net_device *p2p_net; char ioctl_buf[256]; int ioctl_buf_sync; };
struct wiphy { struct bcm_cfg80211 *private; };
#define wiphy_priv(w) ((w)->private)
static int logged, calls, primary_error, fallback_error;
static struct cfg80211_gtk_rekey_data expected;
#define wldev_iovar_setbuf(dev,name,data,size,buf,len,lock) send_keys(name,data)
static int send_keys(const char *name, const void *data) {
    ++calls;
    if (!strcmp(name, "bcol_gtk_rekey_ptk")) {
        const bcol_gtk_para_t *p = data;
        assert(p->enable == 1 && p->ptk_len == 64);
        assert(!memcmp(p->ptk, expected.kck, 16));
        assert(!memcmp(p->ptk + 16, expected.kek, 16));
        return primary_error;
    }
    assert(!strcmp(name, "gtk_key_info"));
    const gtk_keyinfo_t *p = data;
    assert(!memcmp(p->KCK, expected.kck, 16));
    assert(!memcmp(p->KEK, expected.kek, 16));
    assert(!memcmp(p->ReplayCounter, expected.replay_ctr, 8));
    return fallback_error;
}
static s32
''' + gtk + r'''
int main(void) {
    struct net_device wifi = {0}, p2p = {0};
    struct dhd_conf conf = {.rekey_offload = 1};
    dhd_pub_t pub = {.conf = &conf};
    struct bcm_cfg80211 c = {.p2p_net = &p2p, .pub = &pub};
    struct wiphy w = {.private = &c};
    memset(&expected, 0x5a, sizeof(expected));
    assert(wl_cfg80211_set_rekey_data(&w, &wifi, 0) == -EINVAL);
    assert(wl_cfg80211_set_rekey_data(&w, &p2p, &expected) == -EINVAL);
    assert(calls == 0);
    assert(wl_cfg80211_set_rekey_data(&w, &wifi, &expected) == 0);
    assert(calls == 1 && logged == 0);
    primary_error = -EOPNOTSUPP;
    assert(wl_cfg80211_set_rekey_data(&w, &wifi, &expected) == 0);
    assert(calls == 3 && logged == 0);
    fallback_error = -EIO;
    assert(wl_cfg80211_set_rekey_data(&w, &wifi, &expected) == -EIO);
    assert(calls == 5 && logged == 0);
    conf.rekey_offload = 0;
    assert(wl_cfg80211_set_rekey_data(&w, &wifi, &expected) == BCME_UNSUPPORTED);
    assert(calls == 5 && logged == 0);
    return 0;
}
''')
    profiles(source, config)
    # Also cover the related roaming/private-command key logging sites.
    for name in ("wl_cfg80211.c", "wl_android_ext.c"):
        assert not re.search(r'prhex\("(?:kck|kek|KCK|replay_ctr)',
                             (source / name).read_text()), name
    print("related key-log sites: PASS")


if __name__ == "__main__":
    main()
