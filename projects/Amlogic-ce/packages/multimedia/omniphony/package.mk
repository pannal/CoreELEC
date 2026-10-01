# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026-present Team CoreELEC (https://coreelec.org)

PKG_NAME="omniphony"
PKG_VERSION="a69f591cfdc79da6d5ca6f7db389d75a2c6fae91"
PKG_SHA256="379e2fb73cd2a6fa495b39759d8cc4feb633507ebce266ebf2f26b6bdb073c1c"
PKG_LICENSE="GPL-3.0-or-later"
PKG_SITE="https://github.com/mgth/Omniphony"
# The fork rather than PKG_SITE. It follows the current upstream interfaces and
# retains the Kodi integration pieces that are not upstream:
#
#   - pcm_bridge presents host-decoded PCM as a channel bed, so the codec can
#     render anything ffmpeg decodes rather than only the formats the object
#     decoder handles. It supplies the original codec's source family to the
#     placement policy: DTS and Auro use Sphere; Dolby, PCM and generic sources
#     retain their upstream defaults.
#   - orender_decoded_sample_rate reports the rate the bridge actually decoded
#     at: a host must name a rate before it has seen a packet,
#     and for DTS-HD MA the one it can name is the core's 48 kHz while an XLL
#     extension riding that core decodes at 96, so the codec opens at its guess
#     and re-opens at what the engine says.
#   - orender_drain renders what the engine still holds when the input ends:
#     what the decode thread has not returned yet, then what the decoder is
#     still holding, one packet's audio per call. The helper calls it from
#     FLUSH until it returns nothing and sends the audio before acknowledging
#     end of stream. The Harletty pin implements the paired bridge_api 0.4
#     method.
#   - orender_hrir_in_use names the HRIR set the binaural path is convolving
#     with. The helper passes it on as hrir= and the codec shows it as the head
#     model, so a SOFA file the engine could not load reads Built-in.
#
# ABI 8 supplies the upstream height-tier labels, ABI 9 the NUL-terminated
# orender_source_label query, ABI 10 the decode thread (orender_set_option's
# `decode_thread`, which the helper turns on for TrueHD and E-AC-3) and
# orender_drain, ABI 11 the thread's live option and
# orender_output_packet_pts, and this fork's decoded-rate, decoder-drain and
# HRIR additions are ABI 12. Every optional symbol is probed with dlsym;
# major-version mismatch is still fatal. The build produces both orender_ffi
# and pcm_bridge from this same pin so the C ABI and Rust bridge_api stay
# paired.
PKG_URL="https://github.com/v-lix/Omniphony/archive/${PKG_VERSION}.tar.gz"
# GitHub commit tarballs extract to <repo>-<githash>/, which scripts/unpack
# cannot auto-detect against ${PKG_NAME}-${PKG_VERSION}.
PKG_SOURCE_DIR="Omniphony-${PKG_VERSION}"
PKG_LONGDESC="Omniphony: spatial audio engine. Kodi's binaural codec runs it in a 64-bit helper process to render audio for headphones - Dolby Atmos and DTS:X objects, Auro-3D heights and ordinary channel layouts alike."
PKG_TOOLCHAIN="manual"

# This feature is 64-bit whatever the image is. A shared library takes the word
# size of whoever loads it, so an engine loaded by a 32-bit Kodi would be
# 32-bit, where the same work costs roughly twice as much - measured on an
# S922X, Dolby Digital Plus Atmos decodes at 0.419 of realtime in 32-bit
# against 0.204 in 64-bit. So the engine, the decoder bridge and the helper are
# always built aarch64, and this package has two jobs depending on which pass
# is running it:
#
#   aarch64  build liborender.so, and pull in the other two 64-bit packages.
#            On an aarch64 image that is the end of it - everything installs
#            natively. For a 32-bit image, omniphony-bundle packs what this
#            pass built.
#   arm      compile nothing; install the configuration below, and pull in
#            omniphony-bundle, which installs the 64-bit side - from a pinned
#            download, or with OMNIPHONY_FROM_SOURCE=yes from the aarch64 pass.
if [ "${TARGET_ARCH}" = "aarch64" ]; then
  PKG_DEPENDS_TARGET="toolchain cargo:host harletty-bridge omniphony-helper"
else
  PKG_DEPENDS_TARGET="toolchain omniphony-bundle"
fi

# The cargo workspace sits in a subdirectory of the repository; the rest of the
# repo (the standalone player, the studio GUIs) is not built here.
PKG_OMNIPHONY_MANIFEST="omniphony-renderer/Cargo.toml"

# CDVDAudioCodecOmniphony names five files, all under special://xbmcbin/omniphony/:
# the helper, the engine, the two bridges - libharletty_bridge.so for the
# bitstream formats that carry objects or heights, libpcm_bridge.so for
# everything ffmpeg decodes - and cascade-12.yaml. On this image
# special://xbmcbin resolves to the directory kodi.bin was started from, which
# is /usr/lib/kodi, so the payload sits one level below it.
PKG_OMNIPHONY_DIR="/usr/lib/kodi/omniphony"


make_target() {
  # Only the 64-bit pass compiles anything.
  [ "${TARGET_ARCH}" = "aarch64" ] || return 0

  export RUSTC_LINKER="${CC}"

  # Two crates, one invocation: they share the workspace's dependency graph, so
  # building them together costs barely more than the engine alone.
  cargo build --manifest-path ${PKG_BUILD}/${PKG_OMNIPHONY_MANIFEST} \
              --target ${TARGET_NAME} \
              --release \
              --package orender_ffi \
              --package pcm_bridge
}

makeinstall_target() {
  mkdir -p ${INSTALL}${PKG_OMNIPHONY_DIR}

  # The virtual speaker layout the codec names when it falls back to cascaded
  # rendering. Twelve spatialized positions plus an LFE that is routed rather
  # than placed - a cascaded render costs one convolution per spatialized
  # speaker, so the LFE deliberately carries spatialize: false.
  cp ${PKG_DIR}/config/cascade-12.yaml ${INSTALL}${PKG_OMNIPHONY_DIR}/

  # The engine's own 5.1 and 7.1 room-model layouts, which it looks for by name
  # in a fixed list of directories, the last of them /usr/share/orender/layouts.
  # Kodi names neither, and the engine's built-in positions put every placed
  # channel exactly where these do, but without the files most streams leave
  # two warnings in kodi.log saying they are missing.
  mkdir -p ${INSTALL}/usr/share/orender/layouts/legacy
  cp ${PKG_BUILD}/layouts/legacy/5.1.yaml ${PKG_BUILD}/layouts/legacy/7.1.yaml \
     ${INSTALL}/usr/share/orender/layouts/legacy/

  if [ "${TARGET_ARCH}" = "aarch64" ]; then
    # On a 64-bit image this is the whole job: the engine here, the bridge and
    # the helper from their own packages, all native, nothing to bridge across
    # a word size. For a 32-bit image the same install_pkg is instead what
    # omniphony-bundle packs. Strip here either way, where ${STRIP} is the
    # aarch64 one - the 32-bit pass could not strip these if it tried.
    local _out="${PKG_BUILD}/.${TARGET_NAME}/target/${TARGET_NAME}/release"

    # The real file takes the soname the engine's build.rs stamps into it, and
    # the plain name is the symlink - the codec hands the helper the plain one.
    cp ${_out}/liborender.so ${INSTALL}${PKG_OMNIPHONY_DIR}/liborender.so.0
    ln -sf liborender.so.0 ${INSTALL}${PKG_OMNIPHONY_DIR}/liborender.so

    # The PCM bridge is this package's own build product, where the Harletty
    # bridge beside it comes from a package of its own. No soname dance here:
    # the codec hands the helper this exact name.
    cp ${_out}/libpcm_bridge.so ${INSTALL}${PKG_OMNIPHONY_DIR}/

    debug_strip ${INSTALL}${PKG_OMNIPHONY_DIR}/liborender.so.0 \
                ${INSTALL}${PKG_OMNIPHONY_DIR}/libpcm_bridge.so
  fi
}
