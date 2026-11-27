#!/usr/bin/env python3
"""Exercise real CE package loading/stamps and a cached image-install fixture.

Java compilation and the build scheduler are simulated. Package loading, stamp
calculation and install hooks are production code. --baseline REV reproduces
stale same-version JAR delivery using the old recipes in a temporary checkout.
"""
import argparse
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
RECIPES = {
    'bdj-runtime': 'projects/Amlogic-ce/packages/mediacenter/bdj-runtime/package.mk',
    'jre-libbluray': 'packages/addons/addon-depends/jre-depends/jre-libbluray/package.mk',
    'libbluray': 'packages/multimedia/libbluray/package.mk',
}
BASELINE = None


class RuntimePackaging(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.packages = self.root / 'packages'
        for name, rel in RECIPES.items():
            recipe = self.packages / name / 'package.mk'
            recipe.parent.mkdir(parents=True)
            recipe.write_text(subprocess.check_output(['git', 'show', f'{BASELINE}:{rel}'],
                              cwd=ROOT, text=True) if BASELINE else (ROOT / rel).read_text())
        # A fixed-version shared Java patch and the unpack dependency's recipe.
        self.patch = self.packages / 'libbluray/patches/java.patch'
        self.patch.parent.mkdir()
        self.patch.write_text('old Java patch')
        jdk = self.packages / 'jdk-x86_64-zulu/package.mk'
        jdk.parent.mkdir()
        jdk.write_text('PKG_NAME="jdk-x86_64-zulu"\n')
        version = (self.packages / 'libbluray/package.mk').read_text()
        self.version = re.search(r'^PKG_VERSION="([^"]+)"', version, re.M)[1]
        self.jars = [f'libbluray-j2se-{self.version}.jar',
                     f'libbluray-awt-j2se-{self.version}.jar']
        self.cache = self.root / 'cache'
        self.stamps = {}
        self.image = self.root / 'image'

    def shell(self, package, command, install=None):
        # Full production source_package expands PKG_DEPENDS_UNPACK. Directly
        # sourcing package.mk alone would incorrectly omit the shared patches.
        script = r'''
. "$FUNCTIONS"
get_pkg_directory() { printf '%s/packages/%s' "$ROOT" "${1%%:*}"; }
build_with_debug() { return 1; }
get_install_dir() { printf '%s/cache/%s' "$ROOT" "${1%%:*}"; }
source_package "$PACKAGE"
''' + command
        return subprocess.run(['bash', '-c', script], cwd=self.root, text=True,
            capture_output=True, env={**os.environ, 'NOONEXIT': 'yes',
                'FUNCTIONS': str(ROOT / 'config/functions'), 'ROOT': str(self.root),
                'PROJECT_DIR': str(self.root / 'projects'), 'PROJECT': 'Amlogic-ce',
                'DEVICE': '', 'BUILD': str(self.root / 'build'),
                'MACHINE_HARDWARE_NAME': 'x86_64', 'PACKAGE': package,
                'INSTALL': str(install or self.cache / package)})

    def stamp(self, package):
        result = self.shell(package, 'calculate_stamp')
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def producer_install(self, names, payload=b'new compiled Java'):
        stage = self.cache / 'jre-libbluray'
        shutil.rmtree(stage, ignore_errors=True)
        jars = stage / 'usr/share/java'
        jars.mkdir(parents=True)
        for name in names:
            (jars / name).write_bytes(payload)
        return self.shell('jre-libbluray', 'post_makeinstall_target', stage)

    def build(self, package, payload):
        stamp = self.stamp(package)
        # Same matching-stamp early return as scripts/build, before build hooks.
        if self.stamps.get(package) == stamp:
            return False
        stage = self.cache / package
        if package == 'jre-libbluray':
            result = self.producer_install(self.jars, payload)
        else:
            shutil.rmtree(stage, ignore_errors=True)
            result = self.shell(package, 'makeinstall_target', stage)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.stamps[package] = stamp
        return True

    def install_image(self):
        shutil.rmtree(self.image, ignore_errors=True)
        # scripts/install installs target dependencies then the wrapper payload.
        for package in ['jre-libbluray', 'bdj-runtime']:
            shutil.copytree(self.cache / package, self.image, dirs_exist_ok=True)

    def test_pair_and_addon_classpath_override(self):
        self.build('jre-libbluray', b'fresh')
        self.build('bdj-runtime', b'fresh')
        self.install_image()
        self.assertEqual(sorted(p.name for p in (self.image / 'usr/share/java').iterdir()),
                         sorted(self.jars))
        script = self.image / 'usr/lib/coreelec/bdj-runtime.sh'
        result = subprocess.run(['sh', '-c', '. "$1"; printf "%s" "$LIBBLURAY_CP"',
                                 'sh', str(script)], capture_output=True, text=True, check=True,
                                env={'LIBBLURAY_CP': '/storage/.kodi/addons/tools.jre.zulu/'})
        self.assertEqual(result.stdout, '/usr/share/java/')

    def test_missing_either_jar_fails_producer_install(self):
        for missing in self.jars:
            with self.subTest(missing=missing):
                result = self.producer_install([p for p in self.jars if p != missing])
                self.assertNotEqual(result.returncode, 0)

    def test_stale_version_pair_is_not_a_substitute(self):
        result = self.producer_install(['libbluray-j2se-1.3.4.jar',
                                        'libbluray-awt-j2se-1.3.4.jar'])
        self.assertNotEqual(result.returncode, 0)

    def incremental(self, changed):
        self.build('jre-libbluray', b'old')
        self.build('bdj-runtime', b'old')
        self.install_image()
        changed.write_text(changed.read_text() + '\n# changed same-version input\n')
        self.assertTrue(self.build('jre-libbluray', b'new'), 'producer stamp missed changed input')
        self.assertFalse(self.build('bdj-runtime', b'new'), 'launcher should stay cached')
        self.install_image()
        for name in self.jars:
            self.assertEqual((self.image / 'usr/share/java' / name).read_bytes(), b'new',
                             'cached wrapper overwrote rebuilt same-name JAR')

    def test_incremental_shared_java_patch(self):
        self.incremental(self.patch)

    def test_incremental_producer_recipe(self):
        self.incremental(self.packages / 'jre-libbluray/package.mk')

    def test_incremental_jdk_recipe(self):
        self.incremental(self.packages / 'jdk-x86_64-zulu/package.mk')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--baseline')
    args, remaining = parser.parse_known_args()
    BASELINE = args.baseline
    unittest.main(argv=[__file__, *remaining])
