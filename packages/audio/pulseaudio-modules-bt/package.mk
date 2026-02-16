# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024-present CoreELEC (https://coreelec.org)

PKG_NAME="pulseaudio-modules-bt"
PKG_VERSION="1.5"
PKG_SHA256="ddaf682738be6555098b9a6c4a98532082f1a4ed9b141e97bb2bbecbac275c55"
PKG_LICENSE="GPL"
PKG_SITE="https://github.com/samtheruby/pulseaudio-modules-bt"
PKG_URL="https://github.com/samtheruby/pulseaudio-modules-bt/archive/v${PKG_VERSION}.tar.gz"
PKG_DEPENDS_TARGET="toolchain pulseaudio ldacBT libfreeaptx fdk-aac ffmpeg"
PKG_LONGDESC="Adds Sony LDAC, aptX, aptX HD, AAC codecs (A2DP Audio) support to PulseAudio"
PKG_TOOLCHAIN="cmake"

PKG_CMAKE_OPTS_TARGET="-DCODEC_LDAC=ON \
                       -DCODEC_AAC_FDK=ON \
                       -DCODEC_APTX_FF=ON \
                       -DCODEC_APTX_HD_FF=ON \
                       -DFORCE_LARGEST_PA_VERSION=ON"

pre_configure_target() {
  # pulseaudio-modules-bt expects PulseAudio source in pa/src directory  # The CMakeLists.txt does: include_directories(pa/src)
  # And then includes <pulsecore/core.h>, so it needs pa/src/pulsecore/
  # Link pa/src to PulseAudio build src directory
  rm -rf ${PKG_BUILD}/pa/src
  ln -sf ${BUILD}/build/pulseaudio-17.0/src ${PKG_BUILD}/pa/src
}

post_makeinstall_target() {
  echo "PulseAudio Bluetooth modules with LDAC/aptX/AAC support installed"
}
