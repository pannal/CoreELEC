#!/usr/bin/env python3
"""Run the Amlogic-ng boot script with a temporary sysfs tree and a recording fbset."""
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INIT = ROOT / "projects/Amlogic-ce/devices/Amlogic-ng/initramfs/platform_init"


class BootScalerTest(unittest.TestCase):
    def check_geometry(self, mode, native, output_width, output_height):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fb0 = root / "sys/class/graphics/fb0"
            for name in ("proc", "sys/class/display", "sys/class/amhdmitx/amhdmitx0",
                         "sys/devices/virtual/amhdmitx/amhdmitx0", "sys/class/video",
                         "sys/class/graphics/fb0", "sys/class/graphics/fb1"):
                (root / name).mkdir(parents=True)
            (root / "proc/cmdline").write_text(
                f"BOOT_IMAGE=kernel boot=BOOT disk=DISK vout={mode},en native_4k_gui={int(native)}")
            (root / "sys/class/amhdmitx/amhdmitx0/hpd_state").write_text("1")
            (root / "sys/devices/virtual/amhdmitx/amhdmitx0/frac_rate_policy").write_text("0")
            (root / "sys/class/display/mode").write_text("1080p60hz")
            script = INIT.read_text().replace("/proc/", "./proc/").replace("/sys/", "./sys/")
            result = subprocess.run(["sh", "-e"], cwd=root, text=True, capture_output=True,
                                    input='fbset() { echo "$@"; }\n' + script, check=True)
            scaled = output_height == 2160 and not native
            width, height = (1920, 1080) if scaled else (output_width, output_height)
            self.assertIn(f"-fb /dev/fb0 -g {width} {height} {width} {height * 2} 32",
                          result.stdout.splitlines())
            self.assertEqual((fb0 / "free_scale").read_text().strip(), "0x10001" if scaled else "0")
            if scaled:
                self.assertEqual((fb0 / "free_scale_axis").read_text().strip(), "0 0 1919 1079")
                self.assertEqual((fb0 / "window_axis").read_text().strip(),
                                 f"0 0 {output_width - 1} {output_height - 1}")

    def test_hdmi_modes(self):
        for mode, width, height in (
            ("smpte24hz", 4096, 2160), ("smpte25hz", 4096, 2160),
            ("smpte30hz", 4096, 2160), ("smpte50hz", 4096, 2160), ("smpte60hz", 4096, 2160),
            ("smpte60hz420", 4096, 2160), ("4k2ksmpte", 4096, 2160),
            ("4096x2160p24hz", 4096, 2160), ("2160p24hz", 3840, 2160),
            ("3840x2160p60hz", 3840, 2160), ("4k2k24hz", 3840, 2160),
            ("1080p60hz", 1920, 1080), ("720p50hz", 1280, 720),
        ):
            for native in (False, True):
                with self.subTest(mode=mode, native=native):
                    self.check_geometry(mode, native, width, height)


if __name__ == "__main__":
    unittest.main()
