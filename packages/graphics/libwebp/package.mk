# SPDX-License-Identifier: GPL-2.0-or-later

PKG_NAME="libwebp"
PKG_VERSION="1.6.0"
PKG_SHA256="e4ab7009bf0629fd11982d4c2aa83964cf244cffba7347ecd39019a9e38c4564"
PKG_LICENSE="BSD"
PKG_SITE="https://developers.google.com/speed/webp"
PKG_URL="https://storage.googleapis.com/downloads.webmproject.org/releases/webp/${PKG_NAME}-${PKG_VERSION}.tar.gz"
PKG_DEPENDS_TARGET="toolchain"
PKG_LONGDESC="Libraries for encoding, decoding and manipulating WebP images."
PKG_TOOLCHAIN="cmake"

PKG_CMAKE_OPTS_TARGET="-DBUILD_SHARED_LIBS=ON \
                       -DWEBP_BUILD_LIBWEBPMUX=ON \
                       -DWEBP_BUILD_ANIM_UTILS=OFF \
                       -DWEBP_BUILD_CWEBP=OFF \
                       -DWEBP_BUILD_DWEBP=OFF \
                       -DWEBP_BUILD_GIF2WEBP=OFF \
                       -DWEBP_BUILD_IMG2WEBP=OFF \
                       -DWEBP_BUILD_VWEBP=OFF \
                       -DWEBP_BUILD_WEBPINFO=OFF \
                       -DWEBP_BUILD_WEBPMUX=OFF \
                       -DWEBP_BUILD_EXTRAS=OFF"
