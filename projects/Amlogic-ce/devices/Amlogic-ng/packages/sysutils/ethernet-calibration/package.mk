# SPDX-License-Identifier: GPL-2.0-or-later

PKG_NAME="ethernet-calibration"
PKG_VERSION="1"
PKG_LICENSE="GPL"
PKG_SITE="https://coreelec.org"
PKG_URL=""
PKG_DEPENDS_TARGET="toolchain Python3 iperf ethtool busybox"
PKG_LONGDESC="Native guarded onboard Ethernet calibration"
PKG_TOOLCHAIN="manual"

makeinstall_target() {
  mkdir -p ${INSTALL}/usr/lib/coreelec/ethernet-calibration
  install -m 0644 ${PKG_DIR}/source/*.py ${INSTALL}/usr/lib/coreelec/ethernet-calibration/
  mkdir -p ${INSTALL}/usr/lib/systemd/system/network-base.service.d
  install -m 0644 ${PKG_DIR}/config/ethernet-calibration.conf \
    ${INSTALL}/usr/lib/systemd/system/network-base.service.d/
}

post_install() {
  sed -i 's/\r$//' \
    "${INSTALL}/usr/lib/systemd/system/ethernet-calibration.service" \
    "${INSTALL}/usr/lib/systemd/system/ethernet-calibration-apply.service"
  enable_service ethernet-calibration.service
}
