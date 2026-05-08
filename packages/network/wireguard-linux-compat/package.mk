# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2019-present Team LibreELEC (https://libreelec.tv)
# Copyright (C) 2024-present Team CoreELEC (https://coreelec.org)

PKG_NAME="wireguard-linux-compat"
PKG_VERSION="1.0.20220627"
PKG_SHA256="19b181e5c7d2260c23ecad4ad324425fd8e88200d97a885a2c7d4bd26cd61461"
PKG_LICENSE="GPLv2"
PKG_SITE="https://www.wireguard.com"
PKG_URL="https://git.zx2c4.com/wireguard-linux-compat/snapshot/wireguard-linux-compat-$PKG_VERSION.tar.xz"
PKG_DEPENDS_TARGET="toolchain linux libmnl"
PKG_NEED_UNPACK="$LINUX_DEPENDS"
PKG_LONGDESC="WireGuard VPN kernel module"
PKG_TOOLCHAIN="manual"
PKG_IS_KERNEL_PKG="yes"

make_target() {
  kernel_make KERNELDIR=$(kernel_path) -C src/ module
}

makeinstall_target() {
  mkdir -p ${INSTALL}/$(get_full_module_dir)/${PKG_NAME}
  cp src/*.ko ${INSTALL}/$(get_full_module_dir)/${PKG_NAME}
}
