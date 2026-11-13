# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026-present Team CoreELEC (https://coreelec.org)

PKG_NAME="uas"
PKG_VERSION="1.0"
PKG_LICENSE="GPL"
PKG_SITE=""
PKG_URL=""
PKG_DEPENDS_TARGET="toolchain Python3"
PKG_LONGDESC="Apply the UAS preference before media automount."
PKG_TOOLCHAIN="manual"

makeinstall_target() {
  install -Dm0755 "${PKG_DIR}/scripts/uas-init" "${INSTALL}/usr/lib/libreelec/uas-init"
}
