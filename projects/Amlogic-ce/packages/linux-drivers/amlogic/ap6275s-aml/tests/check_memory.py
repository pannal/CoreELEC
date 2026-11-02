#!/usr/bin/env python3
"""Exercise the donor's allocation fallback and matching free identity check."""
from pathlib import Path
import re
import sys
from check_source import function, run_c

source = Path(sys.argv[1]) / 'bcmdhd.101.10.591.x'
linux = (source / 'dhd_linux.c').read_text()
attach = function(linux, 'dhd_attach')
start = attach.index('\tdhd = wifi_platform_prealloc(adapter, DHD_PREALLOC_DHD_INFO, sizeof(dhd_info_t));')
end = attach.index('\tmemset(dhd, 0, sizeof(dhd_info_t));', start)
allocation = attach[start:end]
freeing = re.search(r'if \(dhd != \(dhd_info_t \*\)dhd_os_prealloc\(dhdp,\s*'
                    r'DHD_PREALLOC_DHD_INFO, 0, FALSE\)\)\s*'
                    r'MFREE\(dhd->pub.osh, dhd, sizeof\(\*dhd\)\);',
                    function(linux, 'dhd_free')).group()
run_c('DHD_INFO pool rejection, dynamic fallback, OOM and free ownership', r'''
#include <assert.h>
#include <stdlib.h>
#define FALSE 0
#define DHD_PREALLOC_DHD_INFO 3
#define DHD_ERROR(args) ((void)0)
/* check_kernel.py verifies this size using the real CE64-bit layout. */
typedef char dhd_info_t[46808];
static dhd_info_t static_pool;
static size_t pool_limit;
static int fail_malloc, allocations, releases;
static void *prealloc(int section, size_t size) {
    assert(section == DHD_PREALLOC_DHD_INFO);
    return size <= pool_limit ? &static_pool : NULL;
}
static void *allocate(size_t size) {
    assert(size == sizeof(dhd_info_t));
    if (fail_malloc) return NULL;
    ++allocations;
    return malloc(size);
}
static void release(void *pointer, size_t size) {
    assert(pointer && pointer != &static_pool && size == sizeof(dhd_info_t));
    ++releases;
    free(pointer);
}
#define wifi_platform_prealloc(adapter,section,size) prealloc(section,size)
#define dhd_os_prealloc(pub,section,size,flag) prealloc(section,size)
#define MALLOC(osh,size) allocate(size)
#define MFREE(osh,pointer,size) release(pointer,size)
static dhd_info_t *get_info(void) {
    dhd_info_t *dhd;
''' + allocation + r'''
    return dhd;
dhd_null_flag:
    return NULL;
}
static void put_info(dhd_info_t *dhd) {
    if (dhd) {
''' + freeing + r'''
    }
}
int main(void) {
    pool_limit = 34 * 1024;
    dhd_info_t *value = get_info();
    assert(value && value != &static_pool && allocations == 1);
    put_info(value);
    assert(releases == 1);
    pool_limit = sizeof(dhd_info_t);
    value = get_info();
    assert(value == &static_pool && allocations == 1);
    put_info(value);
    assert(releases == 1);
    pool_limit = 34 * 1024; fail_malloc = 1;
    assert(get_info() == NULL && allocations == 1 && releases == 1);
    put_info(NULL);
    assert(releases == 1);
    return 0;
}
''')
