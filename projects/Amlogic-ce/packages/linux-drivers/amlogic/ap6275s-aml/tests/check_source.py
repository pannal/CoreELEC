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
    match = re.search(re.escape(name) + r"\([^;{}]*\)\s*\{", text)
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


def main():
    source = Path(sys.argv[1]) / "bcmdhd.100.10.545.x"
    android = (source / "wl_android.c").read_text()
    cfg = (source / "wl_cfg80211.c").read_text()
    config = (source / "dhd_config.c").read_text()
    start = android.index("\t\tchar *country_code = command + strlen(CMD_COUNTRY)")
    end = android.index("#ifdef WL_CFG80211", start)
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
static int parse(char *command) {
''' + country + r'''
    return revinfo;
}
int main(void) {
    assert(parse("COUNTRY US") == -1);
    assert(parse("COUNTRY US/7") == 7);
    assert(parse("COUNTRY DE/0") == 0);
    assert(parse("COUNTRY US/123") == 123);
    assert(parse("COUNTRY US/") == 0);
    assert(parse("COUNTRY US:7") == -1);
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
#define prhex(...) (++logged)
#define bcopy(a,b,n) memcpy(b,a,n)
struct net_device { int unused; };
struct cfg80211_gtk_rekey_data { u8 kck[16], kek[16], replay_ctr[8]; };
typedef struct { u8 KCK[16], KEK[16], ReplayCounter[8]; } gtk_keyinfo_t;
typedef struct { int enable, ptk_len; u8 ptk[64]; } bcol_gtk_para_t;
struct bcm_cfg80211 { struct net_device *p2p_net; char ioctl_buf[256]; int ioctl_buf_sync; };
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
    struct bcm_cfg80211 c = {.p2p_net = &p2p};
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
    return 0;
}
''')
    helper = function(config, "dhd_conf_set_tput_patch")
    override = function(config, "dhd_conf_set_p3i_tput_profile")
    start = config.rindex("\tif (conf->chip == BCM43752_CHIP_ID || conf->chip == BCM4375_CHIP_ID) {")
    profile = config[start:config.index("\n#endif", start)]
    start = config.rindex("#if defined(BCMSDIO) && defined(CUSTOMER_HW_AMLOGIC)")
    sleep = config[start:config.index("\n#endif", start) + len("\n#endif")]
    initial = re.search(r"\tconf->tput_patch = FALSE;", config).group()
    start = config.index('else if (!strncmp("tput_patch=",')
    parser = config[start:config.index("\n\t}", start) + len("\n\t}")]
    run_c("fresh defaults and explicit throughput overrides", r'''
#include <assert.h>
#include <string.h>
#define TRUE 1
#define FALSE 0
#define BCMSDIO 1
#define CUSTOMER_HW_AMLOGIC 1
#define DYNAMIC_MAX_HDR_READ 1
#define DHDTCPACK_SUPPRESS 1
#define CUSTOM_TCPACK_SUPP_RATIO 3
#define CUSTOM_TCPACK_DELAY_TIME 10
#define BCM43752_CHIP_ID 43752
#define BCM4375_CHIP_ID 4375
#define SDPCM_TXGLOM_CPY 1
#define CONFIG_MSG(...) ((void)0)
struct dhd_conf {
    int chip, chiprev, tput_patch, mtu, pktsetsum, dhd_dpc_prio, disable_proptx;
    int frameburst, max_hdr_read, tcpack_sup_ratio, tcpack_sup_delay, txglom_mode;
};
typedef struct { struct dhd_conf *conf; } dhd_pub_t;
static int firstread, dhd_rxbound, dhd_slpauto;
static int p3i_tput_profile = -1, p3i_tput_active = -1;
static void
''' + helper + '\nstatic void\n' + override + '''
static void preinit(dhd_pub_t *dhd) {
    struct dhd_conf *conf = dhd->conf;
''' + initial + "\n" + profile + "\n" + sleep + '''
}
static void configure(dhd_pub_t *dhd, const char *full_param, const char *data) {
    struct dhd_conf *conf = dhd->conf;
    int len_param = 11;
    if (0) {}
''' + parser + r'''
}
int main(void) {
    struct dhd_conf conf = {.chip = BCM43752_CHIP_ID, .chiprev = 2};
    dhd_pub_t d = {.conf = &conf};
    dhd_slpauto = 1;
    preinit(&d);
    assert(!conf.tput_patch && !conf.pktsetsum && dhd_slpauto == 0);
    assert(conf.dhd_dpc_prio == -1 && conf.disable_proptx == -1);
    assert(firstread == 32 && dhd_rxbound == 128 && conf.max_hdr_read == 0);
    configure(&d, "tput_patch=", "1");
    assert(conf.tput_patch && conf.pktsetsum && conf.dhd_dpc_prio == 98);
    assert(firstread == 256 && dhd_rxbound == 512);
    configure(&d, "tput_patch=", "0");
    assert(!conf.tput_patch && !conf.pktsetsum && conf.dhd_dpc_prio == -1);
    assert(firstread == 32 && dhd_rxbound == 128);
    dhd_conf_set_p3i_tput_profile(&d);
    assert(p3i_tput_active == 0);
    configure(&d, "tput_patch=", "1");
    dhd_conf_set_p3i_tput_profile(&d);
    assert(p3i_tput_active == 1);
    p3i_tput_profile = 0;
    dhd_conf_set_p3i_tput_profile(&d);
    assert(p3i_tput_active == 0 && !conf.pktsetsum);
    p3i_tput_profile = 1;
    dhd_conf_set_p3i_tput_profile(&d);
    assert(p3i_tput_active == 1 && conf.pktsetsum);
    conf.chiprev = 1;
    conf.tput_patch = 0;
    dhd_conf_set_p3i_tput_profile(&d);
    assert(p3i_tput_active == -1 && !conf.tput_patch);
    conf.chiprev = 2;
    conf.chip = 4354;
    dhd_conf_set_p3i_tput_profile(&d);
    assert(p3i_tput_active == -1 && !conf.tput_patch);
    conf.chip = BCM43752_CHIP_ID;
    p3i_tput_profile = -1;
    configure(&d, "tput_patch=", "1");
    preinit(&d);
    assert(!conf.tput_patch && !conf.pktsetsum && dhd_slpauto == 0);
    assert(firstread == 32 && dhd_rxbound == 128);
    return 0;
}
''')
    # Also cover the related roaming/private-command key logging sites.
    for name in ("wl_cfg80211.c", "wl_android_ext.c"):
        assert not re.search(r'prhex\("(?:kck|kek|KCK|replay_ctr)',
                             (source / name).read_text()), name
    print("related key-log sites: PASS")


if __name__ == "__main__":
    main()
