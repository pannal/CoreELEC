# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2023-present Team CoreELEC (https://coreelec.org)
#
# Rebuilding the prebuilt tarball:
#
# The default path uses a pre-compiled tarball. To rebuild (e.g. after
# upgrading to a new libdovi version or building from unreleased main):
#
#   CE_TOOLCHAIN="<CE_BUILD>/build.CoreELEC-Amlogic-ng.arm-21/toolchain"
#   CE_CC="${CE_TOOLCHAIN}/bin/armv8a-libreelec-linux-gnueabihf-gcc"
#   CE_AR="${CE_TOOLCHAIN}/bin/armv8a-libreelec-linux-gnueabihf-ar"
#   RUST_TARGET="arm-unknown-linux-gnueabihf"
#
#   # Requires: rustup, cargo-c (cargo install cargo-c),
#   #           rustup target add arm-unknown-linux-gnueabihf
#   export CC_arm_unknown_linux_gnueabihf="$CE_CC"
#   export AR_arm_unknown_linux_gnueabihf="$CE_AR"
#   export CARGO_TARGET_ARM_UNKNOWN_LINUX_GNUEABIHF_LINKER="$CE_CC"
#
#   # Clone/download source:
#   git clone https://github.com/quietvoid/dovi_tool.git && cd dovi_tool
#
#   # Build and install to staging directory:
#   cargo cinstall --manifest-path dolby_vision/Cargo.toml \
#     --target $RUST_TARGET --library-type staticlib \
#     --profile release --prefix /usr --destdir /tmp/libdovi-install
#
#   # Package with required prefix (must match libdovi-${ARCH}-${PKG_VERSION}):
#   VER=3.3.3  # match PKG_VERSION below
#   mkdir -p /tmp/libdovi-pkg/libdovi-arm-${VER}
#   cp -a /tmp/libdovi-install/usr /tmp/libdovi-pkg/libdovi-arm-${VER}/
#   tar cJf libdovi-arm-${VER}.tar.xz -C /tmp/libdovi-pkg libdovi-arm-${VER}
#
#   # Install tarball + sidecar files (CE skips download when all three exist):
#   cp libdovi-arm-${VER}.tar.xz <CE_SRC>/sources/libdovi/
#   sha256sum libdovi-arm-${VER}.tar.xz | cut -d' ' -f1 \
#     > <CE_SRC>/sources/libdovi/libdovi-arm-${VER}.tar.xz.sha256
#   echo "https://sources.coreelec.org/libdovi-arm-${VER}.tar.xz" \
#     > <CE_SRC>/sources/libdovi/libdovi-arm-${VER}.tar.xz.url
#   # Update PKG_SHA256 for "arm" below with the sha256 value

PKG_NAME="libdovi"
PKG_VERSION="3.3.3"
PKG_SITE="https://github.com/quietvoid/dovi_tool"
PKG_DEPENDS_TARGET="toolchain"
if [ "${BUILD_FROM_SRC}" = "yes" ]; then
  PKG_SHA256="8ccb1922d7dbb57bc4f2c15c10b90c462f7a5f292efe317c116db923728dd3f1"
  PKG_URL="https://github.com/quietvoid/dovi_tool/archive/${PKG_NAME}-${PKG_VERSION}.tar.gz"
  PKG_DEPENDS_TARGET+=" cargo-c:host"
else
  case "${TARGET_ARCH}" in
    "arm")
      PKG_SHA256="d1caec483d6bebeebea414347d0244ced398ab7f4e237d90407e3683d453212a"
      ;;
    "aarch64")
      PKG_SHA256="e6e0bb82198a58a58cd38bbb2a6d286ff9d024ad35f490ff4b127ea415521457"
      ;;
  esac
  PKG_SOURCE_NAME="${PKG_NAME}-${ARCH}-${PKG_VERSION}.tar.xz"
  PKG_URL="https://sources.coreelec.org/${PKG_SOURCE_NAME}"
fi
PKG_LICENSE="MIT"
PKG_LONGDESC="dovi_tool is a CLI tool combining multiple utilities for working with Dolby Vision."
PKG_TOOLCHAIN="manual"

if [ "${BUILD_FROM_SRC}" = "yes" ]; then
pre_make_target() {
  CARGO_BASE_OPTS="--manifest-path ${PKG_BUILD}/dolby_vision/Cargo.toml \
                   --target ${TARGET_NAME}"
  CARGO_BUILD_OPTS="--library-type staticlib \
                    --profile release \
                    --prefix /usr
                    ${CARGO_BASE_OPTS}"
}

make_target() {
  cargo fetch ${CARGO_BASE_OPTS}
  cargo cbuild ${CARGO_BUILD_OPTS}
}

makeinstall_target() {
  cargo cinstall ${CARGO_BUILD_OPTS} --destdir ${SYSROOT_PREFIX}
  cargo cinstall ${CARGO_BUILD_OPTS} --destdir ${INSTALL}
}
else
make_target() {
  cp -PR * ${SYSROOT_PREFIX}
}

makeinstall_target() {
  : #
}
fi
