# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2009-2016 Stephan Raue (stephan@openelec.tv)

PKG_NAME="libaacs"
# Upstream through 55be92be9e80a654b7c98d29fec5769b1b1493d9, plus MKB cleanup.
PKG_VERSION="0.12.0"
PKG_SHA256="1996673a9fc45ee4a364c66ffa84756629bf3923e52346c7358b71becb8e4419"
PKG_LICENSE="GPL"
PKG_SITE="http://www.videolan.org/developers/libaacs.html"
PKG_URL="https://download.videolan.org/pub/videolan/libaacs/${PKG_VERSION}/${PKG_NAME}-${PKG_VERSION}.tar.bz2"
PKG_DEPENDS_TARGET="toolchain libgcrypt"
PKG_LONGDESC="Open implementation of the AACS (Advanced Access Content System) specification."
PKG_TOOLCHAIN="autotools"

PKG_CONFIGURE_OPTS_TARGET="--disable-werror \
                           --disable-extra-warnings \
                           --disable-optimizations \
                           --with-libgcrypt-prefix=${SYSROOT_PREFIX}/usr \
                           --with-libgpg-error-prefix=${SYSROOT_PREFIX}/usr \
                           --with-gnu-ld"

post_configure_target() {
  libtool_remove_rpath libtool
}

post_makeinstall_target() {
  mkdir -p ${INSTALL}/usr/config/aacs
    cp -P ../KEYDB.cfg ${INSTALL}/usr/config/aacs
}
