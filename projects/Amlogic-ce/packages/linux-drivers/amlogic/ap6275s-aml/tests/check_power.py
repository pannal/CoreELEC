#!/usr/bin/env python3
"""Verify the extracted Amlogic power callback retains CE settling delays."""
from pathlib import Path
import sys
from check_source import function, run_c

source = Path(sys.argv[1]) / 'bcmdhd.101.10.591.x'
power = function((source / 'dhd_gpio.c').read_text(), 'dhd_wlan_set_power')
run_c('Amlogic power sequence, settling and GPIO failure', r'''
#include <assert.h>
#include <errno.h>
#define CUSTOMER_HW_AMLOGIC 1
#define BCMSDIO 1
#define printf(...) ((void)0)
typedef struct { int gpio_wl_reg_on; } wifi_adapter_info_t;
static int events[8], used, fail_gpio;
static int gpio_direction_output(int gpio, int on) { (void)gpio; (void)on; return fail_gpio; }
static void extern_wifi_set_enable(int on) { events[used++] = on ? -1 : -2; }
static void mdelay(int milliseconds) { events[used++] = milliseconds; }
static int
''' + power + r'''
int main(void) {
    wifi_adapter_info_t adapter = {.gpio_wl_reg_on = -1};
    int expected[] = {-2, 200, -1, 200, 100};
    assert(dhd_wlan_set_power(&adapter, 1) == 0);
    assert(used == 5);
    for (int i = 0; i < used; ++i) assert(events[i] == expected[i]);
    used = 0;
    assert(dhd_wlan_set_power(&adapter, 0) == 0);
    assert(used == 2 && events[0] == -2 && events[1] == 200);
    adapter.gpio_wl_reg_on = 1;
    fail_gpio = 1; used = 0;
    assert(dhd_wlan_set_power(&adapter, 1) == -EIO && used == 0);
    assert(dhd_wlan_set_power(&adapter, 0) == -EIO && used == 0);
    return 0;
}
''')
