# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026-present Team CoreELEC (https://coreelec.org)

PKG_NAME="omniphony-bundle"
# The bundle's own release number, not a source pin. It names the tarball the
# aarch64 pass writes and the GitHub release the arm pass downloads, so a new
# bundle takes a new number before it is built - see makeinstall_target.
PKG_VERSION="2"
# Empty until a bundle is uploaded: the arm pass then refuses to build rather
# than download something unchecked. The aarch64 pass writes the value to use
# beside the tarball.
PKG_SHA256="54fbf7b288f42f60c36dfe06452cc6c5e001f22eed997d7423fbd0a25e8ea33a"
PKG_LICENSE="GPL-3.0-or-later"
PKG_SITE="https://github.com/v-lix/CoreELEC"
PKG_LONGDESC="The 64-bit side of Kodi's binaural codec, built once: the Omniphony engine, its two decoder bridges, the helper process and the 64-bit runtime they start with, packed for a 32-bit image."
PKG_TOOLCHAIN="manual"

# An Amlogic-ng image is 32-bit, but the binaural codec's engine, bridges and
# helper are 64-bit - see omniphony/package.mk for why. Building them takes a
# whole aarch64 tree: its own cross toolchain, glibc and the Rust target. This
# package lets an image build do without that tree, and has a job in each pass:
#
#   aarch64  build the three 64-bit packages, assemble exactly what a 32-bit
#            image ships of them, and pack it as
#            target/omniphony-bundle-${PKG_VERSION}.tar.xz, to be uploaded as a
#            GitHub release, with its checksum and manifest beside it.
#            `ARCH=aarch64 scripts/build omniphony-bundle`. This is also the
#            command OMNIPHONY_FROM_SOURCE=yes needs: it installs this
#            assembly, which `scripts/build omniphony` alone does not make.
#   arm      install that assembly into the image. By default it is the
#            uploaded tarball, pinned below, and nothing 64-bit is built; with
#            OMNIPHONY_FROM_SOURCE=yes it is the aarch64 pass's own result in
#            the sibling build tree, as it was before this package existed.
#
# An aarch64 image does not use this package: omniphony installs everything
# natively there.
if [ "${TARGET_ARCH}" = "aarch64" ]; then
  # glibc and gcc are here for their install trees, not for anything they
  # compile: they own the loader, the C library and libgcc_s that a 64-bit
  # process needs on a 32-bit image. Without them here, gcc:target is never
  # built by a `scripts/build omniphony-bundle` - only the virtual image
  # package pulls it - and libgcc_s.so.1 exists nowhere this pass can find it.
  #
  # The bridge and the helper are named here although omniphony pulls them in
  # too: scripts/build returns on a matching stamp before it looks at a
  # package's dependencies, and omniphony's stamp does not follow theirs, so
  # reached only through it a changed helper or a re-pinned bridge would never
  # be rebuilt - the bundle would pack the old helper under a manifest hashed
  # from the new source, or look for a bridge version that was never built.
  PKG_DEPENDS_TARGET="toolchain omniphony harletty-bridge omniphony-helper glibc gcc"
else
  PKG_DEPENDS_TARGET="toolchain"
  if [ "${OMNIPHONY_FROM_SOURCE}" != "yes" -a -n "${PKG_SHA256}" ]; then
    PKG_URL="https://github.com/v-lix/CoreELEC/releases/download/${PKG_NAME}-${PKG_VERSION}/${PKG_NAME}-${PKG_VERSION}.tar.xz"
  fi
fi

# Switching between the two arm sources has to re-run the arm pass, and the
# variable is not a file the stamp would otherwise see.
PKG_STAMP="${OMNIPHONY_FROM_SOURCE}"

# The stamp has to move with the three packages the bundle is made of. In the
# aarch64 pass, `scripts/build omniphony-bundle` returns on a matching stamp
# before it looks at its dependencies, so without this a re-pinned engine or
# bridge, or a changed helper, left the old bundle in place. In the arm pass it
# makes a change to any of them reach the manifest check below.
PKG_NEED_UNPACK="$(get_pkg_directory omniphony) $(get_pkg_directory harletty-bridge) $(get_pkg_directory omniphony-helper)"

# Where the codec expects to find the payload - see omniphony/package.mk.
PKG_OMNIPHONY_DIR="/usr/lib/kodi/omniphony"

# What the bundle was built from, written into it and checked against this tree
# whenever it is installed, so a bundle that no longer matches the pins - a
# stale download, or an aarch64 tree last built before a re-pin - stops the
# build instead of shipping an engine the codec or the helper was not built
# for. The engine and the Harletty bridge are their source pins. The helper has
# no pin, being this tree's own source, so it is a hash of its package.mk and
# sources/. Build options in the other two package.mk files are not covered:
# changing one is a reason to take a new PKG_VERSION.
omni_bundle_manifest() {
  local _helper="$(get_pkg_directory omniphony-helper)"

  echo "omniphony=$(get_pkg_version omniphony)"
  echo "harletty-bridge=$(get_pkg_version harletty-bridge)"
  echo "omniphony-helper=$(cd "${_helper}" && find package.mk sources -type f | LC_ALL=C sort | xargs sha256sum | sha256sum | cut -d' ' -f1)"
}

omni_bundle_check() {
  local _manifest="${1}" _from="${2}"

  [ -f "${_manifest}" ] || die "omniphony-bundle: ${_from} has no manifest at ${_manifest}"

  if [ "$(cat "${_manifest}")" != "$(omni_bundle_manifest)" ]; then
    die "omniphony-bundle: ${_from} does not match this tree's pins.

  bundle:
$(sed 's/^/    /' "${_manifest}")
  this tree:
$(omni_bundle_manifest | sed 's/^/    /')

Build a matching bundle with
  PROJECT=${PROJECT} DEVICE=${DEVICE} ARCH=aarch64 ${BUILD_SUFFIX:+BUILD_SUFFIX=${BUILD_SUFFIX} }./scripts/build omniphony-bundle
and either pin it here or build the image with OMNIPHONY_FROM_SOURCE=yes."
  fi
}

# ELF header: e_ident[EI_CLASS] at offset 4 is 2 for 64-bit, and the low byte of
# the little-endian e_machine at offset 18 is 183, EM_AARCH64. Read with od
# because it is the same everywhere, where readelf is target-prefixed.
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
  # against. This is the only source that is right by construction rather than
  # by convention.
  ${TARGET_PREFIX}gcc -print-file-name=${_name}

  # The install trees glibc and gcc produce: the same files, and what a 64-bit
  # image would ship.
  echo "$(get_install_dir glibc)/usr/lib/${_name}"
  echo "$(get_install_dir gcc)/usr/lib/${_name}"
  echo "${SYSROOT_PREFIX}/usr/lib/${_name}"
}

omni_install_a64_lib() {
  local _dest="${1}" _name="${2}"
  OMNI_TRIED=""

  omni_try_a64_libs "${_dest}" $(omni_a64_lib_candidates "${_name}") && return 0

  # Not where it was expected. Widen to a bounded search of the toolchain
  # before giving up - reached only when the layout is not what this package
  # believes, which is exactly when guessing harder is worth the walk.
  omni_try_a64_libs "${_dest}" \
    $(find ${TOOLCHAIN} -name "${_name}" -type f 2>/dev/null) && return 0

  die "omniphony-bundle: the aarch64 pass has no ${_name}.
Existing files considered, none of them aarch64 objects:${OMNI_TRIED:-
    (none)}"
}

# The aarch64 pass: assemble the payload under ${1}, laid out from the image's
# root.
omni_bundle_assemble() {
  local _root="${1}"
  local _dir="${_root}${PKG_OMNIPHONY_DIR}"
  local _omni="$(get_install_dir omniphony)"
  local _bridge="$(get_install_dir harletty-bridge)"
  local _helper="$(get_install_dir omniphony-helper)"

  # Taken by explicit path rather than a wildcard, so a missing piece is a hard
  # error instead of a silent gap: the bridge ABI is abi_stable-checked when the
  # engine loads it, and a mismatched pair fails at runtime rather than here.
  mkdir -p ${_dir}/lib
  cp -a ${_omni}${PKG_OMNIPHONY_DIR}/liborender.so.0 ${_dir}/
  ln -sf liborender.so.0 ${_dir}/liborender.so
  cp -a ${_omni}${PKG_OMNIPHONY_DIR}/libpcm_bridge.so ${_dir}/
  cp -a ${_bridge}${PKG_OMNIPHONY_DIR}/libharletty_bridge.so ${_dir}/
  cp -a ${_helper}${PKG_OMNIPHONY_DIR}/omniphony-helper ${_dir}/

  # The 64-bit runtime. A 64-bit process cannot borrow a 32-bit image's
  # libraries, so it brings its own - from the toolchain that built the
  # objects, so the loader, the C library and the objects are one matched set.
  local _lib
  for _lib in libc.so.6 libm.so.6 libgcc_s.so.1; do
    omni_install_a64_lib "${_dir}/lib/${_lib}" "${_lib}"
  done

  # The loader is the one file that cannot live in a private directory: the
  # linker bakes its path into the helper's PT_INTERP, and the kernel resolves
  # that before anything in the process can influence a search. So it goes
  # where the aarch64 ABI says it goes. Nothing collides - a 32-bit image's own
  # loader is ld-linux-armhf.so.3 - and scripts/image links /lib to /usr/lib,
  # so both spellings of the interpreter path resolve to this file.
  mkdir -p ${_root}/usr/lib
  omni_install_a64_lib "${_root}/usr/lib/ld-linux-aarch64.so.1" ld-linux-aarch64.so.1

  # --force-rpath writes DT_RPATH instead of DT_RUNPATH. Either tag would do
  # here, because every object that needs the private directory is given one
  # directly - the engine and the bridges are dlopened rather than linked, so
  # neither is reached through the helper's own tag. RPATH is chosen because it
  # is inherited down the dependency chain where RUNPATH is not, so it keeps
  # covering these four if they pick up a new dependency later. The usual
  # reason to prefer RUNPATH, that it can be overridden with LD_LIBRARY_PATH,
  # does not apply: nothing on the image sets one for Kodi.
  #
  # Every dlopened object needs its own tag, so a bridge added here without
  # being added to this list would load on a developer's box and fail on the
  # image, where libgcc_s lives only in the private directory.
  local _obj
  for _obj in omniphony-helper liborender.so.0 libharletty_bridge.so libpcm_bridge.so; do
    patchelf --force-rpath --set-rpath '$ORIGIN/lib' ${_dir}/${_obj}
  done

  # Strip the stage the way scripts/build strips this package's install
  # directory once makeinstall_target returns. The tarball is packed before
  # that, so without this the download carried the toolchain sysroot's libc,
  # libm and loader with their debug info, about 11 MB more than what
  # OMNIPHONY_FROM_SOURCE=yes installs (4.4 MB of the compressed image), and
  # the helper with the 64 KiB patchelf pads it by. Here, after patchelf,
  # strip rewrites each file without that padding and keeps the rpath it set,
  # and both arm sources install the same files.
  if [ "${BUILD_WITH_DEBUG}" != "yes" ] && flag_enabled "strip" "yes"; then
    ${STRIP} $(find ${_root} -type f) 2>/dev/null || :
  fi

  omni_bundle_manifest > ${_dir}/bundle.manifest
}

makeinstall_target() {
  if [ "${TARGET_ARCH}" = "aarch64" ]; then
    local _top="${PKG_NAME}-${PKG_VERSION}"
    local _stage="${PKG_BUILD}/${_top}"
    local _tarball="${TARGET_IMG}/${_top}.tar.xz"

    rm -rf ${_stage}
    omni_bundle_assemble ${_stage}

    # The arm pass's OMNIPHONY_FROM_SOURCE=yes copies from here.
    mkdir -p ${INSTALL}
    cp -a ${_stage}/. ${INSTALL}/

    # One top-level directory named ${PKG_NAME}-${PKG_VERSION}, which is what
    # scripts/unpack expects of a download. The archive is reproducible -
    # sorted, owned by root, one fixed time, single-threaded xz - so packing
    # the same objects again gives the same file: pinning PKG_SHA256 changes
    # this package's stamp and re-packs it, and the result must still be the
    # file that was uploaded.
    mkdir -p ${TARGET_IMG}
    tar -C ${PKG_BUILD} --sort=name --owner=0 --group=0 --numeric-owner \
        --mtime="2026-01-01 00:00:00Z" -cf - ${_top} | xz -T1 -9 > ${_tarball}
    sha256sum ${_tarball} | cut -d' ' -f1 > ${_tarball}.sha256

    # The manifest again, outside the archive, for the release's description
    # and anything that publishes the bundle - it says what the bundle is
    # without unpacking it.
    cp ${_stage}${PKG_OMNIPHONY_DIR}/bundle.manifest ${TARGET_IMG}/${_top}.manifest

    echo "omniphony-bundle: ${_tarball}"
    echo "  upload it as the asset of release tag ${_top}, then set"
    echo "  PKG_SHA256=\"$(cat ${_tarball}.sha256)\""
    return 0
  fi

  # --- the arm pass: install the assembly into the image --------------------

  local _src

  if [ "${OMNIPHONY_FROM_SOURCE}" = "yes" ]; then
    # config/path composes BUILD from ${TARGET_ARCH} and appends
    # -${BUILD_SUFFIX} when one is set. Nothing else in the name changes
    # between the two passes, and the aarch64 pass is run with the same suffix,
    # so its tree is a deterministic sibling of this one: the suffix comes off,
    # the architecture changes, and the suffix goes back on.
    local _suffix="${BUILD_SUFFIX:+-${BUILD_SUFFIX}}"
    local _a64_build="${BUILD%"${_suffix}"}"
    _a64_build="${_a64_build%".${TARGET_ARCH}-${OS_MAJOR}"}.aarch64-${OS_MAJOR}${_suffix}"

    # PKG_INSTALL is composed from ${BUILD}, which is the only part of the
    # path that differs between the passes.
    _src="${INSTALL/${BUILD}/${_a64_build}}"

    if [ ! -d "${_src}${PKG_OMNIPHONY_DIR}" ]; then
      die "omniphony-bundle: no aarch64 build at ${_src}.

OMNIPHONY_FROM_SOURCE=yes installs what the aarch64 pass built, so that pass
has to happen before the image is built:

  PROJECT=${PROJECT} DEVICE=${DEVICE} ARCH=aarch64 ${BUILD_SUFFIX:+BUILD_SUFFIX=${BUILD_SUFFIX} }./scripts/build omniphony-bundle

That builds the engine, the decoder bridges and the helper. Then build the
image as usual."
    fi
    omni_bundle_check "${_src}${PKG_OMNIPHONY_DIR}/bundle.manifest" "the aarch64 build"
  else
    if [ -z "${PKG_SHA256}" ]; then
      die "omniphony-bundle: no bundle is pinned (PKG_SHA256 is empty).

Either build the image with OMNIPHONY_FROM_SOURCE=yes after
  PROJECT=${PROJECT} DEVICE=${DEVICE} ARCH=aarch64 ${BUILD_SUFFIX:+BUILD_SUFFIX=${BUILD_SUFFIX} }./scripts/build omniphony-bundle
or upload the tarball that writes and pin it in $(get_pkg_directory ${PKG_NAME})/package.mk."
    fi
    _src="${PKG_BUILD}"
    omni_bundle_check "${_src}${PKG_OMNIPHONY_DIR}/bundle.manifest" "${PKG_NAME}-${PKG_VERSION}.tar.xz"
  fi

  mkdir -p ${INSTALL}
  cp -a ${_src}/usr ${INSTALL}/
}
