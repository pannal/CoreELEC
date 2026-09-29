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
#   aarch64  build liborender.so, and pull in the other two 64-bit packages so
#            one command produces the whole 64-bit side. On an aarch64 image
#            that is also the end of it - everything installs natively.
#   arm      compile nothing; assemble what the aarch64 pass left behind into
#            the image, together with the 64-bit runtime it needs to start
#
# glibc and gcc are in the aarch64 list for their install trees, not for
# anything they compile: they own the loader, the C library and libgcc_s that a
# 64-bit process needs on a 32-bit image. Without them here, gcc:target is
# never built by a `scripts/build omniphony` - only the virtual image package
# pulls it - and libgcc_s.so.1 exists nowhere the 32-bit pass can find it.
if [ "${TARGET_ARCH}" = "aarch64" ]; then
  PKG_DEPENDS_TARGET="toolchain cargo:host harletty-bridge omniphony-helper glibc gcc"
else
  PKG_DEPENDS_TARGET="toolchain"
fi

# This package's stamp has to move with the other two. The arm pass copies
# their aarch64 builds into the image, and nothing else would tell it they
# changed; the aarch64 pass is started as `scripts/build omniphony`, which
# returns on a matching stamp before it looks at its dependencies. Without
# this, a new helper or a re-pinned bridge left the image shipping the old one.
PKG_NEED_UNPACK="$(get_pkg_directory harletty-bridge) $(get_pkg_directory omniphony-helper)"

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

# config/path composes BUILD from ${TARGET_ARCH} and appends -${BUILD_SUFFIX}
# when one is set. Nothing else in the name changes between the two passes, and
# the aarch64 pass is run with the same suffix, so its tree is a deterministic
# sibling of this one: the suffix comes off, the architecture changes, and the
# suffix goes back on.
OMNI_BUILD_SUFFIX="${BUILD_SUFFIX:+-${BUILD_SUFFIX}}"
OMNI_A64_BUILD="${BUILD%"${OMNI_BUILD_SUFFIX}"}"
OMNI_A64_BUILD="${OMNI_A64_BUILD%".${TARGET_ARCH}-${OS_MAJOR}"}.aarch64-${OS_MAJOR}${OMNI_BUILD_SUFFIX}"

# Where a package installed to in the aarch64 pass. PKG_INSTALL is composed
# from ${BUILD}, which is the only part of the path that differs between the
# passes, so the substitution is enough - no second copy of the naming rule.
omni_a64_install_dir() {
  local _dir="$(get_install_dir "${1}")"
  echo "${_dir/${BUILD}/${OMNI_A64_BUILD}}"
}

# ELF header: e_ident[EI_CLASS] at offset 4 is 2 for 64-bit, and the low byte of
# the little-endian e_machine at offset 18 is 183, EM_AARCH64. Read with od
# because readelf is target-prefixed in this pass and an unprefixed one is not
# guaranteed to be on PATH.
omni_is_aarch64() {
  [ -f "${1}" ] || return 1
  [ "$(od -An -tu1 -j4 -N1 "${1}" | tr -d ' ')" = "2" ] || return 1
  [ "$(od -An -tu1 -j18 -N1 "${1}" | tr -d ' ')" = "183" ]
}

# Install the first candidate that really is an aarch64 object, or return 1.
# The architecture check is part of the choice rather than an assertion after
# it, so a host copy sharing a name is skipped instead of being fatal.
omni_try_a64_libs() {
  local _dest="${1}" _cand
  shift

  for _cand in "$@"; do
    [ -e "${_cand}" ] || continue
    OMNI_TRIED+="
    ${_cand}"
    if omni_is_aarch64 "${_cand}"; then
      cp -L "${_cand}" "${_dest}"
      return 0
    fi
  done
  return 1
}

# Where one 64-bit runtime library is expected to be, best first.
omni_a64_lib_candidates() {
  local _name="${1}"

  # The compiler that built the objects knows which copy they were linked
  # against, and it is a host binary, so this pass can simply ask it. This is
  # the only source that is right by construction rather than by convention.
  [ -n "${OMNI_A64_CC}" ] && ${OMNI_A64_CC} -print-file-name=${_name}

  # The install trees glibc and gcc produce in that pass: the same files, and
  # what a 64-bit image would ship.
  echo "$(omni_a64_install_dir glibc)/usr/lib/${_name}"
  echo "$(omni_a64_install_dir gcc)/usr/lib/${_name}"
  echo ${OMNI_A64_BUILD}/toolchain/aarch64-*/sysroot/usr/lib/${_name}
}

omni_install_a64_lib() {
  local _dest="${1}" _name="${2}"
  OMNI_TRIED=""

  omni_try_a64_libs "${_dest}" $(omni_a64_lib_candidates "${_name}") && return 0

  # Not where it was expected. Widen to a bounded search of that pass's whole
  # toolchain before giving up - reached only when the layout is not what this
  # package believes, which is exactly when guessing harder is worth the walk.
  omni_try_a64_libs "${_dest}" \
    $(find ${OMNI_A64_BUILD}/toolchain -name "${_name}" -type f 2>/dev/null) && return 0

  die "omniphony: the aarch64 pass has no ${_name}.
Existing files considered, none of them aarch64 objects:${OMNI_TRIED:-
    (none)}"
}

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
    # a word size. On a 32-bit image the same install_pkg is instead what the
    # 32-bit pass below copies from. Strip here either way, where ${STRIP} is
    # the aarch64 one - the 32-bit pass could not strip these if it tried.
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
    return 0
  fi

  # --- the 32-bit pass: assemble what the image ships -----------------------

  if [ ! -d "${OMNI_A64_BUILD}" ]; then
    die "omniphony: no aarch64 build at ${OMNI_A64_BUILD}.

Binaural audio decodes and renders in a 64-bit helper process, so that pass
has to happen before the image is built:

  PROJECT=${PROJECT} DEVICE=${DEVICE} ARCH=aarch64 ${BUILD_SUFFIX:+BUILD_SUFFIX=${BUILD_SUFFIX} }./scripts/build omniphony

That builds the engine, the decoder bridges and the helper. Then build the
image as usual."
  fi

  local _a64_omni="$(omni_a64_install_dir omniphony)"
  local _a64_bridge="$(omni_a64_install_dir harletty-bridge)"
  local _a64_helper="$(omni_a64_install_dir omniphony-helper)"

  # Taken by explicit path rather than a wildcard, so a tree left over from an
  # older version is a hard error instead of a silent mismatch: the bridge ABI
  # is abi_stable-checked when the engine loads it, and a mismatched pair fails
  # at runtime rather than here.
  local _dir
  for _dir in "${_a64_omni}" "${_a64_bridge}" "${_a64_helper}"; do
    [ -d "${_dir}${PKG_OMNIPHONY_DIR}" ] || \
      die "omniphony: the aarch64 pass has not installed ${_dir}${PKG_OMNIPHONY_DIR}"
  done

  cp -a ${_a64_omni}${PKG_OMNIPHONY_DIR}/liborender.so.0 ${INSTALL}${PKG_OMNIPHONY_DIR}/
  ln -sf liborender.so.0 ${INSTALL}${PKG_OMNIPHONY_DIR}/liborender.so
  cp -a ${_a64_omni}${PKG_OMNIPHONY_DIR}/libpcm_bridge.so ${INSTALL}${PKG_OMNIPHONY_DIR}/
  cp -a ${_a64_bridge}${PKG_OMNIPHONY_DIR}/libharletty_bridge.so ${INSTALL}${PKG_OMNIPHONY_DIR}/
  cp -a ${_a64_helper}${PKG_OMNIPHONY_DIR}/omniphony-helper ${INSTALL}${PKG_OMNIPHONY_DIR}/

  # The 64-bit runtime. A 64-bit process cannot borrow this image's libraries,
  # so it brings its own - taken from the aarch64 pass that built the objects,
  # so the loader, the C library and the objects are one matched set.
  #
  # That pass's compiler is a host binary sitting in the sibling tree, so it
  # runs here and can be asked directly where each library is, rather than this
  # pass having to know the toolchain's layout.
  OMNI_A64_CC="$(ls ${OMNI_A64_BUILD}/toolchain/bin/aarch64-*-gcc 2>/dev/null | head -1)"

  mkdir -p ${INSTALL}${PKG_OMNIPHONY_DIR}/lib
  local _lib
  for _lib in libc.so.6 libm.so.6 libgcc_s.so.1; do
    omni_install_a64_lib "${INSTALL}${PKG_OMNIPHONY_DIR}/lib/${_lib}" "${_lib}"
  done

  # The loader is the one file that cannot live in a private directory: the
  # linker bakes its path into the helper's PT_INTERP, and the kernel resolves
  # that before anything in the process can influence a search. So it goes
  # where the aarch64 ABI says it goes. Nothing collides - this image's own
  # loader is ld-linux-armhf.so.3 - and scripts/image links /lib to /usr/lib,
  # so both spellings of the interpreter path resolve to this file.
  mkdir -p ${INSTALL}/usr/lib
  omni_install_a64_lib "${INSTALL}/usr/lib/ld-linux-aarch64.so.1" ld-linux-aarch64.so.1

  # --force-rpath writes DT_RPATH instead of DT_RUNPATH. Either tag would do
  # here, because every object that needs the private directory is given one
  # directly - the engine and the bridge are dlopened rather than linked, so
  # neither is reached through the helper's own tag. RPATH is chosen because it
  # is inherited down the dependency chain where RUNPATH is not, so it keeps
  # covering these four if they pick up a new dependency later. The usual
  # reason to prefer RUNPATH, that it can be overridden with LD_LIBRARY_PATH,
  # does not apply: nothing on this image sets one for Kodi.
  #
  # Every dlopened object needs its own tag, so a bridge added here without
  # being added to this list would load on a developer's box and fail on the
  # image, where libgcc_s lives only in the private directory.
  local _obj
  for _obj in omniphony-helper liborender.so.0 libharletty_bridge.so libpcm_bridge.so; do
    patchelf --force-rpath --set-rpath '$ORIGIN/lib' ${INSTALL}${PKG_OMNIPHONY_DIR}/${_obj}
  done
}
