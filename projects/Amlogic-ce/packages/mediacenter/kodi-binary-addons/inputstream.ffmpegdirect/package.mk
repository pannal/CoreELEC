# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2020-present Team LibreELEC (https://libreelec.tv)

# Project-level override: Piers branch is FFmpeg 8.x clean (uses AV_PROFILE_*,
# av_packet_side_data_get on coded_side_data, AVFMT_NOTIMESTAMPS flag). The
# 21.3.8-Omega upstream pin won't compile against our FFmpeg 8.1 sysroot.
# 22.2.7 includes the parser fix and precedes the FFmpeg 9 requirement.

PKG_NAME="inputstream.ffmpegdirect"
PKG_VERSION="22.2.7-Piers"
PKG_SHA256="90f0bfc4a3e5fe397749f5244608ee8881c6722c9a68c33fd61eb5becd976f23"
PKG_REV="1"
# Vendored-build version: 666.<upstream>-p3i.<iter>. The 666. prefix keeps p3i
# builds permanently ahead of any 0-665.x upstream/third-party variant, so
# Kodi's update check never tries to swap our purpose-built binary for an
# ABI-incompatible stock one. Bump -p3i.N for source rebuilds on the same
# upstream; bump the .<upstream> portion to match upstream version bumps.
PKG_ADDON_VERSION="666.22.2.7-p3i.1"
PKG_ARCH="any"
PKG_LICENSE="GPL2+"
PKG_SITE="https://github.com/xbmc/inputstream.ffmpegdirect"
PKG_URL="https://github.com/xbmc/inputstream.ffmpegdirect/archive/${PKG_VERSION}.tar.gz"
PKG_DEPENDS_TARGET="toolchain kodi-platform bzip2 ffmpeg gmp libpng libxml2 xz zlib zvbi"
PKG_SECTION=""
PKG_SHORTDESC="inputstream.ffmpegdirect"
PKG_LONGDESC="InputStream Client for streams that can be opened by FFmpeg's libavformat such as plain TS, HLS and DASH (without DRM) streams."

PKG_IS_ADDON="yes"
