#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
set -Eeuo pipefail
package=$(cd "$(dirname "$0")/.." && pwd)
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT
# Substitute only the fixed device-node pathname; execute the installed script.
sed "s|^node=.*|node=$out/phy_inhibit|" "$package/scripts/aml-display-wake.sh" > "$out/cleanup"
sh "$out/cleanup" # absent node on older kernels
printf '1\n' > "$out/phy_inhibit"
sh "$out/cleanup"
test "$(cat "$out/phy_inhibit")" = 0
sh "$out/cleanup" # repeated abnormal-exit cleanup is harmless
test "$(cat "$out/phy_inhibit")" = 0
# An existing directory cannot be opened for writing, even by root. File
# permissions alone do not reliably inject a write failure under that user.
rm "$out/phy_inhibit"
mkdir "$out/phy_inhibit"
if sh "$out/cleanup" 2>/dev/null; then
  echo 'expected a failed node write to report failure' >&2
  exit 1
fi
# Both hooks must survive, with display cleanup first if safe-mode recovery fails.
mapfile -t hooks < <(sed -n '/^ExecStopPost=/p' "$package/system.d/kodi.service")
test "${hooks[0]}" = 'ExecStopPost=-/usr/lib/kodi/aml-display-wake.sh'
test "${hooks[1]}" = 'ExecStopPost=-/usr/lib/kodi/kodi-safe-mode'
echo 'Display cleanup script and service hook tests passed.'
