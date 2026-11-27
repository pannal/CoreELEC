# SPDX-License-Identifier: GPL-2.0

PKG_NAME="ap6275s-aml"
PKG_VERSION="f08f1b88be470d07667a671b5c6b7f4e198a75f8"
PKG_SHA256="139de5d2e5b9fd41908f72c5d894954ac3592cb86cca9071c7528f0a51663dca"
PKG_ARCH="arm aarch64"
PKG_LICENSE="GPL"
PKG_SITE="https://github.com/CoreELEC/ap6xxx-aml"
PKG_URL="${PKG_SITE}/archive/${PKG_VERSION}.tar.gz"
PKG_SOURCE_DIR="ap6xxx-aml-${PKG_VERSION}"
PKG_DEPENDS_TARGET="toolchain linux"
PKG_NEED_UNPACK="${LINUX_DEPENDS}"
PKG_LONGDESC="Broadcom AP6275S SDIO WLAN driver 101.10.591.109.61 for Amlogic-ng"
PKG_IS_KERNEL_PKG="yes"
PKG_TOOLCHAIN="manual"

post_unpack() {
  # Keep the bus component distinct from the dhd_sdio composite module.
  mv ${PKG_BUILD}/bcmdhd.101.10.591.x/dhd_sdio.c \
    ${PKG_BUILD}/bcmdhd.101.10.591.x/dhd_sdio_bus.c
}

pre_make_target() {
  unset LDFLAGS
}

make_target() {
  kernel_make -C ${PKG_BUILD}/bcmdhd.101.10.591.x \
    M=${PKG_BUILD}/bcmdhd.101.10.591.x \
    PWD=${PKG_BUILD}/bcmdhd.101.10.591.x \
    KERNEL_SRC=$(kernel_path) \
    CONFIG_BCMDHD_DISABLE_WOWLAN=y \
    CONFIG_BCMDHD_SDIO=y \
    CONFIG_BCMDHD_STATIC_IF=n \
    CONFIG_BCMDHD_FW_SIGNATURE=n \
    CONFIG_BCMDHD_REQUEST_FW=n \
    bcmdhd_sdio
}

makeinstall_target() {
  mkdir -p ${INSTALL}/$(get_full_module_dir)/${PKG_NAME}
  cp ${PKG_BUILD}/bcmdhd.101.10.591.x/dhd_sdio.ko \
    ${INSTALL}/$(get_full_module_dir)/${PKG_NAME}/
}
