# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2009-2016 Stephan Raue (stephan@openelec.tv)
# Copyright (C) 2018-present Team LibreELEC (https://libreelec.tv)

PKG_NAME="curl"
PKG_VERSION="8.23.0"
PKG_SHA256="8dadd7d2536a314f982ad05a651b713f426bf7d02cc2c76cffb9796f2218e6c5"
PKG_LICENSE="MIT"
PKG_SITE="https://curl.se"
PKG_URL="https://curl.se/download/${PKG_NAME}-${PKG_VERSION}.tar.xz"
PKG_DEPENDS_TARGET="toolchain zlib openssl libidn2 nghttp2"
PKG_LONGDESC="Client and library for (HTTP, HTTPS, FTP, ...) transfers."
PKG_TOOLCHAIN="configure"

PKG_CONFIGURE_OPTS_TARGET="--disable-debug \
                           --enable-optimize \
                           --enable-warnings \
                           --disable-ares \
                           --enable-largefile \
                           --enable-http \
                           --enable-ftp \
                           --enable-file \
                           --disable-ldap \
                           --disable-ldaps \
                           --enable-rtsp \
                           --enable-proxy \
                           --disable-dict \
                           --disable-telnet \
                           --disable-tftp \
                           --disable-pop3 \
                           --disable-imap \
                           --disable-smtp \
                           --disable-gopher \
                           --disable-mqtt \
                           --disable-websockets \
                           --disable-manual \
                           --enable-libgcc \
                           --enable-ipv6 \
                           --enable-versioned-symbols \
                           --enable-threaded-resolver \
                           --enable-verbose \
                           --disable-sspi \
                           --enable-cookies \
                           --enable-symbol-hiding \
                           --with-gnu-ld \
                           --without-gssapi \
                           --with-zlib \
                           --without-brotli \
                           --without-zstd \
                           --without-gnutls \
                           --with-openssl \
                           --without-mbedtls \
                           --with-ca-bundle=/run/libreelec/cacert.pem \
                           --without-ca-path \
                           --without-libpsl \
                           --without-libssh2 \
                           --with-libidn2 \
                           --with-nghttp2"

post_configure_target() {
  libtool_remove_rpath libtool
}

post_makeinstall_target() {
  rm -rf ${INSTALL}/usr/share/zsh

  rm -rf ${INSTALL}/usr/bin/${PKG_NAME}-config
  cp ${PKG_NAME}-config ${TOOLCHAIN}/bin
  sed -e "s:\(['= ]\)/usr:\\1${PKG_ORIG_SYSROOT_PREFIX}/usr:g" -i ${TOOLCHAIN}/bin/${PKG_NAME}-config
  chmod +x ${TOOLCHAIN}/bin/${PKG_NAME}-config
}
