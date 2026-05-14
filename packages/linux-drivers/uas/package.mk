# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026-present Team CoreELEC (https://coreelec.org)

PKG_NAME="uas"
PKG_VERSION="1.0"
PKG_LICENSE="GPL"
PKG_SITE=""
PKG_URL=""
PKG_DEPENDS_TARGET="toolchain linux"
PKG_NEED_UNPACK="$LINUX_DEPENDS"
PKG_LONGDESC="USB Attached SCSI (UAS) driver, built out-of-tree from the running kernel's drivers/usb/storage/ sources. CONFIG_USB_UAS is intentionally not set in-tree so usb-storage handles devices in BOT mode by default; this package ships uas.ko for opt-in loading via modprobe."
PKG_IS_KERNEL_PKG="yes"
PKG_TOOLCHAIN="manual"

unpack() {
  mkdir -p ${PKG_BUILD}
  cp $(kernel_path)/drivers/usb/storage/uas.c        ${PKG_BUILD}/
  cp $(kernel_path)/drivers/usb/storage/uas-detect.h ${PKG_BUILD}/
  cp $(kernel_path)/drivers/usb/storage/scsiglue.h   ${PKG_BUILD}/
  cp $(kernel_path)/drivers/usb/storage/unusual_uas.h ${PKG_BUILD}/
  cp $(kernel_path)/drivers/usb/storage/usb.h        ${PKG_BUILD}/
  cat ${PKG_DIR}/extra_uas_entries.h >> ${PKG_BUILD}/unusual_uas.h
  echo "obj-m := uas.o" > ${PKG_BUILD}/Kbuild
}

make_target() {
  LDFLAGS="" make -C $(kernel_path) M=${PKG_BUILD} \
    ARCH=${TARGET_KERNEL_ARCH} \
    KSRC=$(kernel_path) \
    CROSS_COMPILE=${TARGET_KERNEL_PREFIX} \
    modules
}

makeinstall_target() {
  mkdir -p ${INSTALL}/$(get_full_module_dir)/${PKG_NAME}
    cp ${PKG_BUILD}/uas.ko ${INSTALL}/$(get_full_module_dir)/${PKG_NAME}/

  # Prevent udev from autoloading uas.ko when a UAS-capable device is
  # plugged in. Explicit `modprobe uas` still works (blacklist only
  # affects modalias-driven autoload). With CONFIG_USB_UAS unset in the
  # kernel, usb-storage handles UAS-capable devices in BOT mode safely
  # until the user opts in.
  mkdir -p ${INSTALL}/usr/lib/modprobe.d
    echo "blacklist uas" > ${INSTALL}/usr/lib/modprobe.d/uas.conf
}
