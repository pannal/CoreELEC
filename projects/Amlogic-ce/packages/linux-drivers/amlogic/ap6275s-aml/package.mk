# SPDX-License-Identifier: GPL-2.0

PKG_NAME="ap6275s-aml"
PKG_VERSION="f0130717e2cdfc0d339a10e5b430ef15ee6b0c29"
PKG_SHA256="50cb2fe0e91cc40351fe81264217af4067ef10f7d1bd77fd92b85b752afc53bb"
PKG_ARCH="arm aarch64"
PKG_LICENSE="GPL"
PKG_SITE="https://github.com/CoreELEC/ap6xxx-aml"
PKG_URL="${PKG_SITE}/archive/${PKG_VERSION}.tar.gz"
PKG_SOURCE_DIR="ap6xxx-aml-${PKG_VERSION}"
PKG_DEPENDS_TARGET="toolchain linux"
PKG_NEED_UNPACK="${LINUX_DEPENDS}"
PKG_LONGDESC="Broadcom AP6275S SDIO WLAN driver 100.10.545.16 for Amlogic-ng"
PKG_IS_KERNEL_PKG="yes"
PKG_TOOLCHAIN="manual"

pre_make_target() {
  unset LDFLAGS
}

make_target() {
  kernel_make -C ${PKG_BUILD}/bcmdhd.100.10.545.x \
    M=${PKG_BUILD}/bcmdhd.100.10.545.x \
    PWD=${PKG_BUILD}/bcmdhd.100.10.545.x \
    KERNEL_SRC=$(kernel_path) \
    CONFIG_BCMDHD_DISABLE_WOWLAN=y \
    CONFIG_BCMDHD_SDIO=y \
    bcmdhd_sdio
}

makeinstall_target() {
  mkdir -p ${INSTALL}/$(get_full_module_dir)/${PKG_NAME}
  cp ${PKG_BUILD}/bcmdhd.100.10.545.x/dhd.ko \
    ${INSTALL}/$(get_full_module_dir)/${PKG_NAME}/dhd_sdio.ko
}
