# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2009-2016 Stephan Raue (stephan@openelec.tv)
# Copyright (C) 2018-present Team LibreELEC (https://libreelec.tv)

PKG_NAME="libass"
PKG_VERSION="fadc390583f24eb5cf98f16925fd3adee50bca88" # master 2026-01-03, post-0.17.4
PKG_SHA256="fd8226ee4b1e1df98585fd007800611abb560a3fa4d983d2ecfbea7299c58cb2"
PKG_LICENSE="BSD"
PKG_SITE="https://github.com/libass/libass"
PKG_URL="https://github.com/libass/libass/archive/${PKG_VERSION}.tar.gz"
PKG_DEPENDS_TARGET="toolchain freetype fontconfig fribidi harfbuzz"
PKG_LONGDESC="A portable subtitle renderer for the ASS/SSA (Advanced Substation Alpha/Substation Alpha) subtitle format."
PKG_TOOLCHAIN="autotools"

PKG_CONFIGURE_OPTS_TARGET="--disable-test \
                           --enable-fontconfig \
                           --disable-libunibreak \
                           --disable-silent-rules \
                           --with-gnu-ld"

if [ ${TARGET_ARCH} = "x86_64" ]; then
  PKG_DEPENDS_TARGET+=" nasm:host"
  PKG_CONFIGURE_OPTS_TARGET+=" --enable-asm"
fi

post_configure_target() {
  libtool_remove_rpath libtool
}
