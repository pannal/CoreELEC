# SPDX-License-Identifier: GPL-2.0-or-later

PKG_NAME="bdj-runtime"
PKG_VERSION="1"
PKG_LICENSE="LGPL"
PKG_URL=""
PKG_DEPENDS_TARGET="toolchain jre-libbluray"
PKG_LONGDESC="Matching libbluray Java classes for the image's native BD-J library"
PKG_TOOLCHAIN="manual"

makeinstall_target() {
  # jre-libbluray owns the JARs. Do not cache a second copy here: target
  # dependencies rebuild independently, and a cached wrapper installs last.
  mkdir -p "${INSTALL}/usr/lib/coreelec"
  echo 'export LIBBLURAY_CP=/usr/share/java/' > "${INSTALL}/usr/lib/coreelec/bdj-runtime.sh"
}
