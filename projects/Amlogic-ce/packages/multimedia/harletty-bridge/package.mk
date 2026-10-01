# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026-present Team CoreELEC (https://coreelec.org)

PKG_NAME="harletty-bridge"
PKG_VERSION="8f4d88f6f9281d05cdaccab5191205ed8787ab9f"
PKG_SHA256="9bdcd9970ed9d536dc34ea882f24c45cc3c8fa75a6473501b4121ffa07059d9c"
PKG_LICENSE="Apache-2.0"
PKG_SITE="https://github.com/harletty/harletty-bridge"
# The fork rather than PKG_SITE. Its parent is the updated main branch, which
# now supplies the former fork fixes, source labels/families, Auro layouts,
# corrected DTS side positions and DTS-HD HRA decoding (including the examined
# lossy-carrier DTS:X 7.1.4 form). Two feature commits are retained. One decides
# a presentation from one parse, avoids redundant bed clones and drains the
# final buffered access unit. The other forwards the bridge's own log records
# to the host - not its decoders' per-block traces - and reports a DTS:X
# extension that never decodes, so those warnings reach kodi.log rather than
# being dropped.
#
# It compiles against the matching Omniphony bridge_api 0.4 pin. FFmpeg patches
# 0005/0008 identify lossless DTS:X forms and 0010 identifies DTS:X carried in
# DTS-HD HRA before Kodi chooses a decoder; plain HRA remains on FFmpeg.
PKG_URL="https://github.com/v-lix/harletty-bridge/archive/${PKG_VERSION}.tar.gz"
# GitHub commit tarballs extract to <repo>-<githash>/, which scripts/unpack
# cannot auto-detect against ${PKG_NAME}-${PKG_VERSION}.
PKG_SOURCE_DIR="harletty-bridge-${PKG_VERSION}"
PKG_DEPENDS_TARGET="toolchain cargo:host"
# Not for what it links - the bridge is a plugin the engine dlopens, and links
# nothing of Omniphony's - but for what it compiles against: three of its
# crates are path dependencies on the Omniphony checkout. Sources, not objects,
# so this is an unpack dependency and not a build one.
PKG_DEPENDS_UNPACK="omniphony"
PKG_LONGDESC="harletty-bridge: the Dolby, DTS/DTS:X and Auro-3D decoder plugin for the Omniphony renderer. Turns encoded bitstreams into audio plus the source declarations the renderer places in space."
PKG_TOOLCHAIN="manual"

# 64-bit only, and deliberately so. Kodi's binaural codec runs the decode and
# the render in a helper process precisely because this image's userspace is
# 32-bit, where the same work costs roughly twice as much. omniphony-bundle
# packs what this produces for the 32-bit image.
PKG_ARCH="aarch64"

# Where the codec expects to find the bridge - see omniphony/package.mk.
PKG_OMNIPHONY_DIR="/usr/lib/kodi/omniphony"

pre_make_target() {
  # bridge/Cargo.toml reaches out of its own workspace for bridge_api, spdif
  # and sys, as `../../Omniphony/omniphony-renderer/...` - upstream's own
  # comment calls that "the workflow symlink locally and the second checkout in
  # CI". Relative to ${PKG_BUILD}/bridge that lands in ${BUILD}/build, where
  # both packages are unpacked, so the symlink is all that is missing.
  ln -sfn "$(get_build_dir omniphony)" "${BUILD}/build/Omniphony"
}

make_target() {
  export RUSTC_LINKER="${CC}"

  # No `neon` feature: it gates the 32-bit ARM QMF path, and on aarch64 the
  # vector unit is baseline and the compiler is already using it.
  cargo build --manifest-path ${PKG_BUILD}/Cargo.toml \
              --target ${TARGET_NAME} \
              --release \
              --package harletty-bridge
}

makeinstall_target() {
  # This pass builds no image; it installs so omniphony-bundle has somewhere to
  # pack from. Strip here, where ${STRIP} is the aarch64 one.
  mkdir -p ${INSTALL}${PKG_OMNIPHONY_DIR}
  cp ${PKG_BUILD}/.${TARGET_NAME}/target/${TARGET_NAME}/release/libharletty_bridge.so \
     ${INSTALL}${PKG_OMNIPHONY_DIR}/

  debug_strip ${INSTALL}${PKG_OMNIPHONY_DIR}/libharletty_bridge.so
}
