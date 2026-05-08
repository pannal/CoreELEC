# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2022-present Team CoreELEC (https://coreelec.org)

PKG_NAME="CoreELEC-settings"
PKG_VERSION="a24cd5ad7068e101f8328f0514e2807427d6d6d5"
PKG_SHA256="121c9f8bd24b2dcf258ade8caed63b32c2dfd8eaa9e42288cc5d01badaf122c0"
PKG_LICENSE="GPL"
PKG_SITE="https://coreelec.org"
# Using local service.coreelec.settings checkout for development
PKG_URL="file://${ROOT}/../service.coreelec.settings"
PKG_SOURCE_NAME="service.coreelec.settings-local"
PKG_DEPENDS_TARGET="toolchain Python3 connman dbussy"
PKG_LONGDESC="CoreELEC-settings: is a settings dialog for CoreELEC"

pre_unpack() {
  local source_path="${ROOT}/../service.coreelec.settings"

  [ "${PKG_URL}" = "file://${source_path}" ] || die "${PKG_NAME}: expected local source ${source_path}, got ${PKG_URL}"
  [ -d "${source_path}" ] || die "${PKG_NAME}: local source ${source_path} not found"
}

PKG_MAKE_OPTS_TARGET="DISTRONAME=${DISTRONAME} ADDON_VERSION=${ADDON_VERSION} ROOT_PASSWORD=${ROOT_PASSWORD}"

if [ "${DISPLAYSERVER}" = "x11" ]; then
  PKG_DEPENDS_TARGET="${PKG_DEPENDS_TARGET} setxkbmap"
else
  PKG_DEPENDS_TARGET="${PKG_DEPENDS_TARGET} bkeymaps"
fi

post_makeinstall_target() {
  mkdir -p ${INSTALL}/usr/lib/coreelec
    cp ${PKG_DIR}/scripts/* ${INSTALL}/usr/lib/coreelec

  ADDON_INSTALL_DIR=${INSTALL}/usr/share/kodi/addons/service.coreelec.settings

  ${TOOLCHAIN}/bin/python -Wi -t -B ${TOOLCHAIN}/lib/${PKG_PYTHON_VERSION}/compileall.py ${ADDON_INSTALL_DIR}/resources/lib/ -f
  rm -rf $(find ${ADDON_INSTALL_DIR}/resources/lib/ -name "*.py")

  ${TOOLCHAIN}/bin/python -Wi -t -B ${TOOLCHAIN}/lib/${PKG_PYTHON_VERSION}/compileall.py ${ADDON_INSTALL_DIR}/oe.py -f
  rm -rf ${ADDON_INSTALL_DIR}/oe.py
}

post_install() {
  enable_service backup-restore.service
  enable_service factory-reset.service
}
