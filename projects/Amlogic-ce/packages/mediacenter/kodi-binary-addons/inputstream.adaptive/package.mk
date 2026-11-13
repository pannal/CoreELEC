# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2016-present Team LibreELEC (https://libreelec.tv)

# Project-level override: stays on Omega (the matching line for our Kodi 21.3)
# with the CBCS / HLS+ClearKey backport from
# projects/Amlogic-ce/patches/inputstream.adaptive/. Piers source drops
# kodi::addon::StreamCryptoSession (Kodi 22 ABI change), so we don't move to
# Piers until our Kodi base does too.
#
# Vendored-build version: 666.<upstream>-p3i.<iter>. The 666. prefix keeps p3i
# builds permanently ahead of any 0-665.x upstream/third-party variant, so
# Kodi's update check never tries to swap our purpose-built binary for an
# ABI-incompatible stock one.

PKG_NAME="inputstream.adaptive"
PKG_VERSION="21.5.26-Omega"
PKG_SHA256="c6fca33834d6bbba70766a0a1795cf75a6e6e3a1836e67a0bbae721df3de617e"
PKG_REV="1"
PKG_ADDON_VERSION="666.21.5.26-p3i.1"
PKG_ARCH="any"
PKG_LICENSE="GPL"
PKG_SITE="https://github.com/xbmc/inputstream.adaptive"
PKG_URL="https://github.com/xbmc/inputstream.adaptive/archive/${PKG_VERSION}.tar.gz"
PKG_DEPENDS_TARGET="toolchain kodi-platform bento4 nss pugixml rapidjson"
PKG_SECTION=""
PKG_SHORTDESC="inputstream.adaptive"
PKG_LONGDESC="inputstream.adaptive"

PKG_IS_ADDON="yes"

addon() {
  install_binary_addon ${PKG_ADDON_ID}
}
