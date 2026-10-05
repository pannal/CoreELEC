# SPDX-License-Identifier: GPL-2.0-or-later
PKG_NAME="dovi5-prepare"
PKG_VERSION="1"
PKG_LICENSE="GPL"
PKG_SITE=""
PKG_URL=""
PKG_DEPENDS_TARGET="toolchain linux Python3"
PKG_SHORTDESC="Prepare an optional second Dolby Vision backend"
PKG_LONGDESC="Validates an explicitly supported user-provided module against the configured kernel and shim, retains module CRC checks, and prepares a private copy. Original Dolby Vision remains required. No proprietary module is included. ENABLE=no in /storage/.config/dovi5.conf disables preparation and loading."
PKG_TOOLCHAIN="manual"

makeinstall_target() {
  install -d "${INSTALL}/usr/lib/coreelec"
  install -m 0755 "${PKG_DIR}/scripts/dovi5_prepare.py" "${INSTALL}/usr/lib/coreelec/dovi5-prepare"
  install -m 0644 "${PKG_DIR}/scripts/dv5_patch.py" "${INSTALL}/usr/lib/coreelec/dv5_patch.py"
  install -m 0644 "${PKG_DIR}/scripts/canary-profile.json" "${INSTALL}/usr/lib/coreelec/canary-profile.json"
  local dovi5_kernel_install
  dovi5_kernel_install=$(get_install_dir linux)
  [ -s "${dovi5_kernel_install}/.image/Module.symvers" ] || die "Configured kernel Module.symvers missing for dovi5 preparation"
  install -m 0644 "${dovi5_kernel_install}/.image/Module.symvers" "${INSTALL}/usr/lib/coreelec/dovi5-Module.symvers"
  install -d "${INSTALL}/usr/lib/systemd/system" "${INSTALL}/etc" "${INSTALL}/usr/config"
  install -m 0644 "${PKG_DIR}/system.d/dovi5-prepare.service" "${INSTALL}/usr/lib/systemd/system/dovi5-prepare.service"
  install -m 0644 "${PKG_DIR}/config/dovi5.conf" "${INSTALL}/etc/dovi5.conf"
  install -m 0644 "${PKG_DIR}/config/dovi5.conf.sample" "${INSTALL}/usr/config/dovi5.conf.sample"
}

post_install() {
  enable_service dovi5-prepare.service
}
