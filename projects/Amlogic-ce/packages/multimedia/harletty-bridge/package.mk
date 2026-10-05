# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026-present Team CoreELEC (https://coreelec.org)

PKG_NAME="harletty-bridge"
PKG_VERSION="f3211cccdfe78d2e4d6d0c53b16b8e56fdd2a3c7"
PKG_SHA256="e164c4e5eb6c810449b02c7cea576acc032e9730048cf63306829cacb00afe4b"
# The sources are Apache-2.0, but the library links bridge_api, spdif and sys
# from Omniphony, which are GPL-3.0-or-later, so the built bridge is too - as
# bridge/Cargo.toml states.
PKG_LICENSE="GPL-3.0-or-later"
PKG_SITE="https://github.com/harletty/harletty-bridge"
# The fork rather than PKG_SITE. Its parent is the updated main branch, which
# now supplies the former fork fixes, source labels/families, Auro layouts,
# corrected DTS side positions and DTS-HD HRA decoding (including the examined
# lossy-carrier DTS:X 7.1.4 form), recomputes the bed fold of a DTS:X object
# the encoder panned into the bed from its position, inspects each E-AC-3
# access unit at most once, and builds releases with thin LTO and its TrueHD
# decoder from its own truehd fork, a git dependency cargo fetches with the
# rest. Two feature commits are retained. One hands the decoded core back from
# object reconstruction rather than cloning it for every object frame, and
# drains the final buffered access unit. The other forwards the bridge's own log
# records to the host - not its decoders' per-block traces - and reports a DTS:X
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

  # Nothing to enable for the vector code: the E-AC-3 QMF and IMDCT choose
  # their NEON kernels by target_arch, so every aarch64 build takes them, and
  # the AVX2 and AVX-512 paths are x86-64 only.
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
