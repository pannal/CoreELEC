# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026-present Team CoreELEC (https://coreelec.org)

PKG_NAME="omniphony-helper"
PKG_VERSION="1.0"
PKG_LICENSE="GPL-2.0-or-later"
PKG_SITE="https://coreelec.org"
# The source is this directory's sources/, which scripts/unpack copies into
# ${PKG_BUILD} on its own; there is nothing to download.
PKG_URL=""
PKG_DEPENDS_TARGET="toolchain"
# For orender.h. The helper links nothing of the engine - it dlopens it at the
# path the codec hands it in the OPEN command - so this is the header and
# nothing else.
PKG_DEPENDS_UNPACK="omniphony"
PKG_LONGDESC="The 64-bit process Kodi's binaural codec talks to: it loads the Omniphony engine, feeds it whole encoded access units or decoded PCM, and writes back rendered binaural stereo."
PKG_TOOLCHAIN="manual"

# 64-bit only, which is the entire reason this process exists. A shared library
# takes the word size of whoever loads it, so an engine loaded by Kodi would be
# 32-bit; measured on an S922X, Dolby Digital Plus Atmos decodes at 0.419 of
# realtime in 32-bit against 0.204 in 64-bit, and the process boundary itself
# costs nothing. omniphony-bundle packs this binary for the 32-bit image.
PKG_ARCH="aarch64"

# Where the codec expects to find the helper - see omniphony/package.mk.
PKG_OMNIPHONY_DIR="/usr/lib/kodi/omniphony"

make_target() {
  # -ldl for glibc versions that still keep dlopen out of libc. Since 2.34 it
  # is in libc and libdl is a stub kept for exactly this kind of link line.
  ${CC} ${CFLAGS} ${LDFLAGS} \
    -I$(get_build_dir omniphony)/omniphony-renderer/orender_ffi/include \
    -o ${PKG_BUILD}/omniphony-helper \
    ${PKG_BUILD}/omniphony-helper.c \
    -ldl
}

makeinstall_target() {
  # This pass builds no image; it installs so omniphony-bundle has somewhere to
  # pack from. Strip here, where ${STRIP} is the aarch64 one.
  mkdir -p ${INSTALL}${PKG_OMNIPHONY_DIR}
  cp ${PKG_BUILD}/omniphony-helper ${INSTALL}${PKG_OMNIPHONY_DIR}/

  debug_strip ${INSTALL}${PKG_OMNIPHONY_DIR}/omniphony-helper
}
