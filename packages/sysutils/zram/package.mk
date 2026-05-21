# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026-present Team CoreELEC (https://coreelec.org)

PKG_NAME="zram"
PKG_VERSION=""
PKG_LICENSE="GPL"
PKG_SITE=""
PKG_URL=""
PKG_DEPENDS_TARGET="toolchain"
PKG_LONGDESC="Compressed RAM-backed swap (zram) — adds OOM headroom for memory-pressured workloads. Build defaults in /etc/zram.conf; user overrides in /storage/.config/zram.conf."
PKG_TOOLCHAIN="manual"

makeinstall_target() {
  mkdir -p ${INSTALL}/usr/lib/libreelec
    cp ${PKG_DIR}/scripts/zram-swap ${INSTALL}/usr/lib/libreelec/

  mkdir -p ${INSTALL}/etc
    sed -e "s,@ZRAM_SIZE_MB@,${ZRAM_SIZE_MB},g" \
        -e "s,@ZRAM_ENABLED_DEFAULT@,${ZRAM_ENABLED_DEFAULT},g" \
        ${PKG_DIR}/config/zram.conf > ${INSTALL}/etc/zram.conf

  # Discoverable user-override template, copied to /storage/.config by
  # userconfig-setup on first boot (rename .sample to activate).
  mkdir -p ${INSTALL}/usr/config
    cp ${PKG_DIR}/config/zram.conf.sample ${INSTALL}/usr/config/
}

post_install() {
  enable_service zram-swap.service
}
