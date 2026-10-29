#!/usr/bin/env python3
"""Check real Kbuild naming and model the small DHD udev dispatch rule.

This never loads a module. With the optional CE compiler arguments it compiles
only existing generated metadata, not driver code or a linked kernel module.
"""

import argparse
import fnmatch
from pathlib import Path
import re
import shlex
import subprocess
import tempfile


def kbuild_name(source, kernel, temporary):
    modpost = (kernel / 'scripts/Makefile.modpost').read_text()
    name_rule = next(line for line in modpost.splitlines()
                     if line.startswith('modname = '))
    harness = temporary / 'inspect.mk'
    harness.write_text(
        f'include {source}/Makefile\n' + name_rule + '\n'
        f'$(file >{temporary}/module,$(MODULE_NAME))\n'
        f'$(file >{temporary}/objects,$(obj-m))\n'
        f'$(file >{temporary}/components,$($(MODULE_NAME)-objs))\n'
        '.PHONY: inspect\ninspect: $(MODULE_NAME).mod.o\n'
        '$(MODULE_NAME).mod.o:\n'
        f'\t$(file >{temporary}/metadata-name,$(modname))\n\t@:\n'
    )
    subprocess.run(['make', '--no-print-directory', '-f', str(harness), 'inspect',
                    'CONFIG_BCMDHD_SDIO=y', 'CONFIG_CFG80211=y',
                    'CONFIG_WIRELESS_EXT=y', 'src=' + str(source)], check=True)
    name = (temporary / 'module').read_text().strip()
    objects = (temporary / 'objects').read_text().split()
    components = (temporary / 'components').read_text().split()
    assert name == 'dhd_sdio', name
    assert objects == ['dhd_sdio.o'], objects
    assert 'dhd_sdio.o' not in components, 'composite object contains itself'
    assert 'dhd_sdio_bus.o' in components
    assert (source / 'dhd_sdio_bus.c').is_file()
    assert not (source / 'dhd_sdio.c').exists()
    metadata_name = (temporary / 'metadata-name').read_text().strip()
    assert metadata_name == name
    return metadata_name


def metadata_identity(name, kernel, command_file, compiler, temporary):
    # Reuse the configured CE metadata compiler flags, changing only the name,
    # output and dependency-file destination. There is no driver/module link.
    command = shlex.split(command_file.read_text().splitlines()[0].split(' := ', 1)[1])
    metadata = Path(command[-1])
    assert '.name = KBUILD_MODNAME' in metadata.read_text()
    flags = []
    index = 1
    while index < len(command) - 1:
        argument = command[index]
        if argument == '-o':
            index += 2
            continue
        if argument == '-c' or argument.startswith('-Wp,-MD,'):
            index += 1
            continue
        if argument.startswith('-DKBUILD_MODNAME='):
            argument = '-DKBUILD_MODNAME="' + name + '"'
        flags.append(argument)
        index += 1
    output = temporary / 'metadata.o'
    subprocess.run([str(compiler), *flags, '-c', '-o', str(output), str(metadata)],
                   cwd=kernel, check=True)
    observed = subprocess.check_output(
        ['readelf', '-p', '.gnu.linkonce.this_module', str(output)], text=True)
    assert re.search(r'\]\s+' + re.escape(name) + r'\s*$', observed, re.M), observed
    print('CE compiled metadata identity: ' + name)


def strip_target(source, kernel, temporary):
    # Execute the actual wrapper target with its kernel build replaced by true
    # and its strip tool replaced by a file-name checker. No module is built.
    directory = temporary / 'output'
    directory.mkdir()
    (directory / 'dhd_sdio.ko').write_text('fixture')
    strip = temporary / 'test-strip'
    strip.write_text(
        '#!/usr/bin/env python3\nfrom pathlib import Path\nimport sys\n'
        'assert sys.argv[1] == "--strip-unneeded"\n'
        f'assert Path(sys.argv[2]).resolve() == Path({str(directory / "dhd_sdio.ko")!r})\n'
        'assert Path(sys.argv[2]).is_file()\n')
    strip.chmod(0o755)
    subprocess.run(['make', '--no-print-directory', '-f', str(source / 'Makefile'),
                    'bcmdhd_sdio', 'MAKE=true', 'CONFIG_BCMDHD_SDIO=y',
                    'src=' + str(source), 'M=' + str(directory),
                    'KERNEL_SRC=' + str(kernel),
                    'CROSS_COMPILE=' + str(temporary / 'test-')], check=True)
    print('actual wrapper strip target: PASS (kernel build disabled)')


def evaluate_rules(text, event):
    """Evaluate this rule's match/assignment/GOTO subset without udev actions."""
    lines = [line.strip() for line in text.splitlines()
             if line.strip() and not line.lstrip().startswith('#')]
    labels = {}
    for index, line in enumerate(lines):
        match = re.fullmatch(r'LABEL="([^"]+)"', line)
        if match:
            assert match[1] not in labels
            labels[match[1]] = index
    event, requests, index = dict(event), [], 0
    while index < len(lines):
        jump = None
        for part in lines[index].split(','):
            match = re.fullmatch(
                r'\s*(ACTION|SUBSYSTEM|ENV\{[^}]+\}|RUN\{builtin\}|GOTO|LABEL)'
                r'\s*(==|!=|\+=|=)\s*"([^"]*)"\s*', part)
            assert match, part
            key, operator, value = match.groups()
            environment = key.startswith('ENV{')
            field = key[4:-1] if environment else key
            if operator in ('==', '!='):
                matches = fnmatch.fnmatchcase(event.get(field, ''), value)
                if matches != (operator == '=='):
                    break
            elif key == 'GOTO':
                jump = labels[value]
                break
            elif key == 'LABEL':
                pass
            elif environment and operator == '=':
                event[field] = value
            elif key == 'RUN{builtin}' and operator == '+=':
                requests.append(value)
            else:
                raise AssertionError(part)
        index = jump + 1 if jump is not None else index + 1
    return event, requests


def dispatch(source, rules):
    text = rules.read_text()
    table = (source / 'bcmsdh_sdmmc_linux.c').read_text()
    table = table.split('static const struct sdio_device_id bcmsdh_sdmmc_ids[] = {', 1)[1].split('};', 1)[0]
    names = re.findall(r'SDIO_DEVICE\(SDIO_VENDOR_ID_BROADCOM, (\w+)\)', table)
    header = (source / 'include/bcmdevs.h').read_text()
    identifiers = {int(re.search(r'^#define\s+' + name + r'\s+(0x[0-9a-fA-F]+)',
                                header, re.M)[1], 16) for name in names}
    assert len(identifiers) == 10
    # Retain legacy autoload for these IDs until hardware acceptance.
    legacy = {0x4354, 0xa9bf}
    assert legacy <= identifiers
    cases = 0
    for identifier in sorted(identifiers | {0x4334, 0x4329, 0xffff}):
        alias = f'sdio:c00v02D0d{identifier:04X}'
        for action in ('add', 'change', 'remove'):
            event = {'ACTION': action, 'SUBSYSTEM': 'sdio',
                     'MODALIAS': alias, 'SDIO_ID': f'02D0:{identifier:04X}'}
            observed, requests = evaluate_rules(text, event)
            if action == 'add' and identifier in identifiers:
                module = 'dhd' if identifier in legacy else 'dhd_sdio'
                assert observed['MODALIAS'] == module, observed
                assert requests == ['kmod load ' + module], requests
                # Stock 80-drivers.rules also reads the final MODALIAS property.
                assert set([observed['MODALIAS'], *[r.split()[-1] for r in requests]]) == {module}
            else:
                assert observed == event, (observed, event)
                assert requests == [], requests
            cases += 1
    for identifier in (0xaae8, *sorted(legacy)):
        for event in (
                {'ACTION': 'add', 'SUBSYSTEM': 'usb', 'MODALIAS': 'usb:example',
                 'SDIO_ID': f'02D0:{identifier:04X}'},
                {'ACTION': 'add', 'SUBSYSTEM': 'sdio',
                 'SDIO_ID': f'02D0:{identifier:04X}'},
                {'ACTION': 'add', 'SUBSYSTEM': 'sdio', 'MODALIAS': '',
                 'SDIO_ID': f'02D0:{identifier:04X}'},
                {'ACTION': 'add', 'SUBSYSTEM': 'sdio', 'MODALIAS': 'other',
                 'SDIO_ID': f'1234:{identifier:04X}'}):
            assert evaluate_rules(text, event) == (event, [])
            cases += 1
    print(f'udev dispatch model: {cases} cases pass')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='unpacked and patched package root')
    parser.add_argument('rules', type=Path)
    parser.add_argument('--kernel', type=Path, required=True)
    parser.add_argument('--metadata-command', type=Path)
    parser.add_argument('--compiler', type=Path)
    arguments = parser.parse_args()
    source = arguments.source / 'bcmdhd.100.10.545.x'
    with tempfile.TemporaryDirectory(prefix='dhd-identity-') as temporary:
        temporary = Path(temporary)
        name = kbuild_name(source, arguments.kernel, temporary)
        print('Kbuild identity/components and modpost name: PASS')
        strip_target(source, arguments.kernel, temporary)
        if arguments.metadata_command:
            assert arguments.compiler, '--compiler is required with --metadata-command'
            metadata_identity(name, arguments.kernel, arguments.metadata_command,
                              arguments.compiler, temporary)
    dispatch(source, arguments.rules)


if __name__ == '__main__':
    main()
