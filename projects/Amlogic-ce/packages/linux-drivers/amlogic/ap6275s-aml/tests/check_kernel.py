#!/usr/bin/env python3
"""Check the selected driver using actual CE kernel flags without building modules.

Provide the configured kernel, compiler and a recorded kernel .o.cmd file from
this CE build. Only the common kernel flags are reused; driver flags come from
its actual Makefile. Temporary files and syntax-only probes stay outside it.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
import shlex
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--kernel', type=Path, required=True)
    parser.add_argument('--compiler', type=Path, required=True)
    parser.add_argument('--kernel-command', type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve() / 'bcmdhd.101.10.591.x'
    kernel = args.kernel.resolve()
    original = shlex.split(args.kernel_command.read_text().splitlines()[0].split(' := ', 1)[1])
    end = original.index('-Wno-packed-not-aligned') + 1
    base = [item for item in original[1:end] if not item.startswith('-Wp,-MD,')]
    assert '-D__KERNEL__' in base and '-fsyntax-only' not in base
    with tempfile.TemporaryDirectory(prefix='ap6275s-kernel-') as temporary:
        temporary = Path(temporary)
        harness = temporary / 'inspect.mk'
        harness.write_text(
            f'include {kernel}/include/config/auto.conf\ninclude {source}/Makefile\n'
            f'$(file >{temporary}/flags,$(EXTRA_CFLAGS))\n'
            f'$(file >{temporary}/objects,$(DHDOFILES))\n'
            '.PHONY: inspect\ninspect: ; @:\n')
        subprocess.run(['make', '--no-print-directory', '-f', str(harness), 'inspect',
                        'src=' + str(source), 'M=' + str(source), 'KERNEL_SRC=' + str(kernel),
                        'CONFIG_BCMDHD_SDIO=y', 'CONFIG_BCMDHD_DISABLE_WOWLAN=y',
                        'CONFIG_BCMDHD_STATIC_IF=n', 'CONFIG_BCMDHD_FW_SIGNATURE=n',
                        'CONFIG_BCMDHD_REQUEST_FW=n'], cwd=kernel, check=True)
        flags = shlex.split((temporary / 'flags').read_text())
        assert '-Wno-error' not in flags and '-Wno-address' not in flags
        for prohibited in ('-DWL_STATIC_IF', '-DSYNA_FW_SIGNATURE', '-DFW_SIGNATURE',
                           '-DDHD_LINUX_STD_FW_API', '-DDHD_REQUEST_FW_PATH', '-DDHD_STATIC_IN_DRIVER'):
            assert prohibited not in flags, prohibited
        for required_flag in ('-D__linux__=1', '-DDHD_SUPPORT_VFS_CALL', '-DDHD_TPUT_PATCH',
                              '-DCUSTOMER_HW_AMLOGIC', '-DENABLE_INSMOD_NO_FW_LOAD',
                              '-DOOB_INTR_ONLY', '-DDISABLE_WOWLAN'):
            assert required_flag in flags, required_flag
        assert '-DPOWERUP_MAX_RETRY=3' in flags
        objects = list(dict.fromkeys((temporary / 'objects').read_text().split()))
        files = [source / Path(item).with_suffix('.c') for item in objects]
        assert all(file.is_file() for file in files)

        def check(file, extra=''):
            actual = file
            if extra:
                actual = temporary / ('probe-' + file.name)
                actual.write_text(f'#include "{file}"\n' + extra)
            command = [str(args.compiler.resolve()), *base, *flags, '-DMODULE', '-mcmodel=large',
                       '-Wframe-larger-than=3000', '-DKBUILD_BASENAME="' + file.stem + '"',
                       '-DKBUILD_MODNAME="dhd_sdio"', '-fsyntax-only', str(actual)]
            result = subprocess.run(command, cwd=kernel, text=True, capture_output=True)
            return {'file': file.name, 'returncode': result.returncode,
                    'diagnostics': result.stdout + result.stderr}

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(check, files))
        for result in results:
            if result['returncode'] or result['diagnostics']:
                print(result['file'] + ':\n' + result['diagnostics'])
        assert all(result['returncode'] == 0 for result in results)
        print(f'actual CE syntax-only translation units: {len(results)}/{len(results)} PASS')
        pool_source = (kernel / 'drivers/amlogic/wifi/dhd_static_buf.c').read_text()
        # These comparisons apply to the actual CE4.9 platform allocator contract.
        for name, value in [('DHD_PREALLOC_DHD_INFO_SIZE', 34),
                            ('WLAN_DHD_WLFC_BUF_SIZE', 64),
                            ('DHD_PREALLOC_DHD_WLFC_HANGER_SIZE', 73),
                            ('DHD_PREALLOC_DATABUF_SIZE', 64),
                            ('DHD_PREALLOC_WIPHY_ESCAN0_SIZE', 64)]:
            assert re.search(r'#define\s+' + name + r'\s+\(' + str(value) + r'\s*\*\s*1024\)', pool_source), name
        assert re.search(r'#define\s+CUSTOM_LOG_DUMP_BUFSIZE_MB\s+4\b', pool_source)
        probes = {
            'dhd_linux.c': ['sizeof(dhd_info_t)==46808', 'sizeof(dhd_info_t)>34*1024'],
            'dhd_wlfc.c': ['sizeof(athost_wl_status_info_t)<=64*1024',
                          'WLFC_HANGER_SIZE(WLFC_HANGER_MAXITEMS)<=73*1024'],
            'wl_cfg80211.c': ['ESCAN_BUF_SIZE<=64*1024'],
            'dhd_sdio_bus.c': ['MAX_DATA_BUF<=64*1024', 'WLC_IOCTL_MAXLEN+128<24*1024'],
            'dhd_log_dump.c': ['LOG_DUMP_TOTAL_BUFSIZE==4*1024*1024',
                               'LOG_DUMP_SPECIAL_MAX_BUFSIZE==8*1024'],
        }
        assertions = 0
        for name, expressions in probes.items():
            extra = '\n'.join('_Static_assert(' + expr + ', "CE4.9 allocation contract");'
                              for expr in expressions)
            result = check(source / name, extra)
            assert result['returncode'] == 0, result['diagnostics']
            assertions += len(expressions)
        print(f'actual CE allocator/ABI syntax assertions: {assertions} PASS')
        exports = {line.split()[1] for line in (kernel / 'Module.symvers').read_text().splitlines()}
        required = {'bcmdhd_mem_prealloc', 'extern_wifi_set_enable', 'wifi_irq_num',
                    'wifi_irq_trigger_level', 'wifi_get_mac', 'sdio_reinit', 'sdio_reset_comm',
                    'mmc_hw_reset', 'kernel_read', 'vfs_read', 'vfs_write', 'sched_setscheduler'}
        assert required <= exports, sorted(required - exports)
        print('required CE platform/file/reset/scheduler exports: PASS (full modpost pending)')


if __name__ == '__main__':
    main()
