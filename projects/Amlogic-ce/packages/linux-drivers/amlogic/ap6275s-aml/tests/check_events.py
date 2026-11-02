#!/usr/bin/env python3
"""Exercise production event registration with old and new firmware interfaces."""
from pathlib import Path
import re
import sys
from check_source import function, run_c


def main():
    source = Path(sys.argv[1]) / 'bcmdhd.101.10.591.x'
    linux = (source / 'dhd_linux.c').read_text()
    reader = function(linux, 'dhd_p3i_read_event_mask')
    writer = function(linux, 'dhd_p3i_write_event_mask')
    legacy = function(linux, 'dhd_legacy_preinit_ioctls')
    start = legacy.index('\t/* make up event mask ext message iovar for event larger than 128 */')
    start = legacy.index('\t/* Read event_msgs_ext mask */', start)
    end = legacy.index('\n#if defined(DHD_8021X_DUMP)', start)
    registration = legacy[start:end]
    # Preserve the actual firmware event numbers rather than inventing a mask.
    events = (source / 'include/bcmevent.h').read_text()
    constants = []
    required_events = {'WLC_E_SET_SSID', 'WLC_E_LINK', 'WLC_E_ESCAN_RESULT', 'WLC_E_PSK_SUP'}
    for name in sorted(set(re.findall(r'WLC_E_[A-Z0-9_]+', registration)) | required_events):
        match = re.search(r'^#define\s+' + name + r'\s+([0-9]+)', events, re.M)
        assert match, name
        constants.append('#define ' + name + ' ' + match[1])
    run_c('extended/legacy core events, bounded buffers and firmware errors', r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
#include <stdint.h>
#define TRUE 1
#define FALSE 0
#define BCME_OK 0
#define BCME_ERROR -1
#define BCME_UNSUPPORTED -23
#define BCME_VERSION -37
#define BCME_BADLEN -24
#define WLC_IOCTL_SMLEN 256
#define WL_EVENTING_MASK_LEN 16
#define WL_EVENTING_MASK_EXT_LEN 27
#define EVENTMSGS_EXT_STRUCT_SIZE 4
#define EVENTMSGS_VER 1
#define EVENTMSGS_SET_MASK 3
#define DHD_FLAG_IBSS_MODE 1
#define DHD_FLAG_P2P_MODE 2
#define DHD_ERROR(args) ((void)0)
#define bcopy(a,b,n) memcpy(b,a,n)
#define setbit(mask,bit) ((mask)[(bit)/8] |= (1U << ((bit)%8)))
#define clrbit(mask,bit) ((mask)[(bit)/8] &= ~(1U << ((bit)%8)))
#define isset(mask,bit) ((mask)[(bit)/8] & (1U << ((bit)%8)))
#define WL_CFG80211 1
#define BCMSDIO 1
#define DHD_DEBUG 1
#define PNO_SUPPORT 1
#define WLTDLS 1
#define RTT_SUPPORT 1
#define WL_ESCAN 1
#define LIMIT_BORROW 1
#define DHD_LOSSLESS_ROAMING 1
#define GSCAN_SUPPORT 1
#define CUSTOM_EVENT_PM_WAKE 1
#define RSSI_MONITOR_SUPPORT 1
#define DBG_PKT_MON 1
''' + '\n'.join(constants) + r'''
typedef struct { int op_mode; } dhd_pub_t;
typedef struct { uint8_t ver, command, len, maxgetsize; char mask[27]; } eventmsgs_ext_t;
static int ext_result, legacy_result, write_result, ext_reads, old_reads, writes;
static int last_legacy;
static uint8_t observed[WL_EVENTING_MASK_EXT_LEN];
static void dhd_update_sdio_data_prio_map(dhd_pub_t *dhd) { (void)dhd; }
static int dhd_iovar(dhd_pub_t *dhd, int ifidx, const char *name,
                    char *input, int inlen, char *output, int outlen, bool set) {
    (void)dhd; assert(ifidx == 0);
    if (!strcmp(name, "event_msgs_ext")) {
        assert(inlen == EVENTMSGS_EXT_STRUCT_SIZE + WL_EVENTING_MASK_EXT_LEN);
        if (!set) {
            ++ext_reads;
            assert(output && outlen == WLC_IOCTL_SMLEN);
            if (ext_result) return ext_result;
            eventmsgs_ext_t *reply = (eventmsgs_ext_t *)output;
            memset(reply, 0, sizeof(*reply));
            reply->ver = EVENTMSGS_VER; reply->len = WL_EVENTING_MASK_EXT_LEN;
            setbit(reply->mask, 127); setbit(reply->mask, 210);
            return BCME_OK;
        }
        eventmsgs_ext_t *message = (eventmsgs_ext_t *)input;
        assert(message->ver == EVENTMSGS_VER);
        assert(message->command == EVENTMSGS_SET_MASK);
        assert(message->len == WL_EVENTING_MASK_EXT_LEN);
        memcpy(observed, message->mask, sizeof(observed));
        last_legacy = 0;
    } else {
        assert(!strcmp(name, "event_msgs"));
        if (!set) {
            ++old_reads;
            assert(!input && !inlen && outlen == WL_EVENTING_MASK_LEN);
            memset(output, 0, outlen); setbit(output, 127);
            return legacy_result;
        }
        assert(inlen == WL_EVENTING_MASK_LEN);
        memset(observed, 0, sizeof(observed));
        memcpy(observed, input, inlen);
        last_legacy = 1;
    }
    assert(!output && outlen == 0);
    ++writes;
    return write_result;
}
static int
''' + reader + '\nstatic int\n' + writer + r'''
static int configure(dhd_pub_t *dhd) {
    struct { eventmsgs_ext_t message; uint8_t canary[8]; } guarded;
    eventmsgs_ext_t *eventmask_msg = &guarded.message;
    char *mask, iov_buf[WLC_IOCTL_SMLEN];
    int msglen = EVENTMSGS_EXT_STRUCT_SIZE + WL_EVENTING_MASK_EXT_LEN;
    int ret = BCME_OK, ret2;
    bool legacy_events = FALSE;
    memset(&guarded, 0, sizeof(guarded));
    memset(guarded.canary, 0x5a, sizeof(guarded.canary));
    eventmask_msg->ver = EVENTMSGS_VER;
    eventmask_msg->len = WL_EVENTING_MASK_EXT_LEN;
''' + registration + r'''
done:
    for (unsigned i = 0; i < sizeof(guarded.canary); ++i)
        assert(guarded.canary[i] == 0x5a);
    return ret;
}
static void check(int extended, int get_old, int set_result, int expected,
                  int expected_old_reads, int expected_writes) {
    dhd_pub_t device = {0};
    ext_result = extended; legacy_result = get_old; write_result = set_result;
    ext_reads = old_reads = writes = 0;
    assert(configure(&device) == expected);
    assert(ext_reads == 1 && old_reads == expected_old_reads && writes == expected_writes);
    if (writes) {
        assert(isset(observed, 127)); /* Preserve firmware-owned existing bits. */
        assert(isset(observed, WLC_E_SET_SSID));
        assert(isset(observed, WLC_E_LINK));
        assert(isset(observed, WLC_E_ESCAN_RESULT));
        assert(isset(observed, WLC_E_PSK_SUP));
        assert(last_legacy == (extended != BCME_OK));
        if (!last_legacy) assert(isset(observed, 210));
    }
}
int main(void) {
    check(BCME_OK, 0, 0, 0, 0, 1);
    check(BCME_UNSUPPORTED, 0, 0, 0, 1, 1);
    check(BCME_VERSION, 0, 0, 0, 1, 1);
    check(-5, 0, 0, -5, 0, 0);
    check(BCME_UNSUPPORTED, -5, 0, -5, 1, 0);
    check(BCME_OK, 0, -5, -5, 0, 1);
    check(BCME_VERSION, 0, -5, -5, 1, 1);
    dhd_pub_t device = {0}; eventmsgs_ext_t message = {0};
    char buffer[WLC_IOCTL_SMLEN]; bool old = true;
    int length = EVENTMSGS_EXT_STRUCT_SIZE + WL_EVENTING_MASK_EXT_LEN;
    ext_reads = old_reads = writes = 0;
    assert(dhd_p3i_read_event_mask(&device, &message, length-1, buffer,
                                 sizeof(buffer), &old) == BCME_BADLEN);
    assert(dhd_p3i_read_event_mask(&device, &message, length, buffer,
                                 length-1, &old) == BCME_BADLEN);
    assert(dhd_p3i_write_event_mask(&device, &message, length-1, true) == BCME_BADLEN);
    assert(dhd_p3i_read_event_mask(&device, &message, length+1, buffer,
                                 sizeof(buffer), &old) == BCME_BADLEN);
    assert(dhd_p3i_write_event_mask(&device, &message, length+1, false) == BCME_BADLEN);
    assert(!ext_reads && !old_reads && !writes);
    return 0;
}
''')


if __name__ == '__main__':
    main()
