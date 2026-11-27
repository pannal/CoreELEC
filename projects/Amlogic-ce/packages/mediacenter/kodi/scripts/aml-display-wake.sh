#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
# Kodi may exit before releasing persistent HDMI output inhibition. Run even
# after abnormal exit or failed startup; older kernels have no such control.
node=/sys/class/amhdmitx/amhdmitx0/phy_inhibit
if [ -e "$node" ]; then
  printf '0\n' > "$node"
fi
