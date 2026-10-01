# CoreELEC p3i

This is a custom build based on the great work Jamal did with his [U3k line of CE builds](https://github.com/CE-Repo) and by proxy, [CPM](https://github.com/cpm-code), [avdvplus](https://github.com/avdvplus/Builds), and everyone else involved. p3i aims to continue the lineage of U3k by:
* providing a stable daily driver base for especially S922X-based SoC's (Ugoos AM6b+, FireTV Cube 2, ...)
* backporting significant improvements to CoreELEC and Kodi from upstream and other known custom builds
* ensuring full transparency in the development process (all changes are properly committed and comprehensible)
* providing significant performance improvements for complex UIs

p3i doesn't aim to reinvent the wheel, but ultimately, to replace the famous [CPM A14 build](https://github.com/cpm-code/xbmc/releases/tag/CoreELEC-Amlogic-ng.arm-21.2-Omega_20250302082815.A14) as a stable, well maintained, daily driver.

It's directly forked off of [U3k B11](https://github.com/CE-Repo/xbmc/releases/tag/B11), so its core is:
* Kodi Omega 21.3 with significant backports from Kodi 22 upstream
* Python 3.13.11
* FFMPEG 8.1.2

## Building

p3i builds like stock CoreELEC, with two things you need to get right: the build target variables, and the local source checkouts the core packages build from.

### PROJECT, DEVICE and ARCH

The build system picks its target from environment variables. **With nothing set it defaults to `PROJECT=Amlogic-ce`, `DEVICE=Amlogic-ne`, `ARCH=aarch64`**, so a bare `make` builds Amlogic-ne, not the Amlogic-ng (arm) images p3i primarily targets (S922X devices like the Ugoos AM6b+).

Every invocation needs the same variables: `make`, `./scripts/clean`, `./scripts/build`, all of them. Each PROJECT/DEVICE/ARCH combination is its own build tree, so a bare `./scripts/clean kodi` cleans the Amlogic-ne tree while your image builds in the Amlogic-ng one: you never actually cleaned.

```sh
# release image for Amlogic-ng (arm)
PROJECT=Amlogic-ce DEVICE=Amlogic-ng ARCH=arm make release

# clean/rebuild single packages in that same tree
PROJECT=Amlogic-ce DEVICE=Amlogic-ng ARCH=arm ./scripts/clean kodi
PROJECT=Amlogic-ce DEVICE=Amlogic-ng ARCH=arm ./scripts/build libdovi
```

### Setting the defaults permanently

Export them from your shell so every command agrees:

```sh
# bash: ~/.bashrc — zsh: ~/.zshrc
export PROJECT=Amlogic-ce
export DEVICE=Amlogic-ng
export ARCH=arm
```

```fish
# fish: run once (universal variables), or put in ~/.config/fish/config.fish
set -Ux PROJECT Amlogic-ce
set -Ux DEVICE Amlogic-ng
set -Ux ARCH arm
```

Don't use `~/.coreelec/options` for these three: the build system sources that file after the target defaults are resolved, so `DEVICE=Amlogic-ng` set there still leaves `ARCH=aarch64` and you end up with a broken hybrid build tree. That file is fine for build tweaks like `CONCURRENCY_MAKE_LEVEL`, not for target selection.

### Binaural audio: the 64-bit side (Amlogic-ng only)

Kodi renders audio binaurally for headphones (Dolby Atmos and DTS:X objects, Auro-3D heights and ordinary channel layouts alike) by running the decoder and the spatial renderer in a **64-bit helper process**. That is not a preference: a shared library takes the word size of whoever loads it, and in the 32-bit userspace an Amlogic-ng image ships, the same work costs roughly twice as much — measured on an S922X, Dolby Digital Plus Atmos decodes at 0.419 of realtime in 32-bit against 0.204 in 64-bit.

Those three 64-bit pieces — the engine and its PCM bridge (`omniphony`), the decoder bridge (`harletty-bridge`) and the helper (`omniphony-helper`) — together with the 64-bit runtime they start with, travel to the image as one prebuilt **bundle**, the `omniphony-bundle` package. So on `ARCH=arm` a normal build needs no aarch64 build tree at all:

```sh
PROJECT=Amlogic-ce DEVICE=Amlogic-ng ARCH=arm make release
```

It downloads the bundle pinned in `omniphony-bundle/package.mk` and checks it against the `omniphony` and `harletty-bridge` pins and the helper's source in this tree. A bundle built from anything else stops the build and says which pin differs.

**Building the 64-bit side from source instead.** Build it in its own tree, then build the image from that rather than the download:

```sh
# once, and again whenever omniphony, harletty-bridge or omniphony-helper moves
PROJECT=Amlogic-ce DEVICE=Amlogic-ng ARCH=aarch64 ./scripts/build omniphony-bundle

PROJECT=Amlogic-ce DEVICE=Amlogic-ng ARCH=arm OMNIPHONY_FROM_SOURCE=yes make release
```

The first command builds all three pieces and an aarch64 toolchain the first time, which is slow; later runs return at once unless something moved. Forgetting it after a re-pin is caught by the same check. It has to be `omniphony-bundle`, not `omniphony`: `./scripts/build omniphony` builds the engine, bridges and helper but not the assembly with their 64-bit runtime that the image installs, so `OMNIPHONY_FROM_SOURCE=yes` stops and asks for the command above. Packing the tarball on the way adds little to that build; you need not upload it.

**When a bundle stops matching.** The check compares three things only: the `omniphony` pin, the `harletty-bridge` pin, and the helper's `package.mk` and `sources/`. Every other change to the tree, Kodi included, builds with the same bundle. A commit that changes one of the three needs a new bundle pinned in the same commit, or `OMNIPHONY_FROM_SOURCE=yes` until it has one. As long as published releases are kept, an older commit still builds with the bundle it pins.

**Making a new bundle.** With the three pins already as they will be committed, run

```sh
tools/omniphony-bundle-publish            # --dry-run: only say which number it would use
```

It takes the next bundle number (the current one if nothing is pinned under it yet), builds the bundle with the aarch64 command above, publishes it as a GitHub release tagged `omniphony-bundle-<version>` on [v-lix/CoreELEC](https://github.com/v-lix/CoreELEC/releases) - a draft first, so it works with release immutability on - downloads it back to check it, and writes `PKG_VERSION` and `PKG_SHA256` into `omniphony-bundle/package.mk`. Then commit the pins, `PKG_VERSION` and `PKG_SHA256` together. If nothing the bundle is built from changed since the pinned one, it stops instead; `--force` publishes anyway. It needs a fine-grained GitHub token for v-lix/CoreELEC with **Contents: Read and write**, in `OMNIPHONY_BUNDLE_TOKEN` or in `~/.config/omniphony-bundle/token` (mode 600).

By hand it is the same steps: raise `PKG_VERSION`, run the aarch64 command, upload `target/omniphony-bundle-<version>.tar.xz` as the only asset of a release with that tag (its `.manifest` beside it says what it was built from), and set `PKG_SHA256` to the `.tar.xz.sha256` beside it. Never replace the asset of a published release: the pin would no longer match it.

**On `ARCH=aarch64` (Amlogic-ne) there is nothing extra to do.** The image is already 64-bit, so one normal build produces everything, and the bundle is not used.

### Local source checkouts (branch `coreelec-21_local`)

Development happens on `coreelec-21_local`; `coreelec-21` carries the release states it is cut from. The core packages of this fork don't download release tarballs. Their `package.mk` points at a local working copy via a `file://` URL, and whatever is checked out there is what gets built. The build never pulls or resets these checkouts, so verify their state before packaging:

| package.mk | builds from local checkout | source repo |
|---|---|---|
| `projects/Amlogic-ce/packages/mediacenter/kodi` | `sources/kodi/xbmc-local` | [pannal/xbmc](https://github.com/pannal/xbmc/) |
| `projects/Amlogic-ce/packages/linux` | `$HOME/linux-amlogic-local` | [pannal/linux-amlogic](https://github.com/pannal/linux-amlogic) |
| `projects/Amlogic-ce/packages/linux-drivers/amlogic/media_modules-aml` | `sources/media_modules-aml/media_modules-aml-local` | [pannal/media_modules-aml](https://github.com/pannal/media_modules-aml) |
| `projects/Amlogic-ce/packages/mediacenter/CoreELEC-settings` | `sources/service.coreelec.settings/service.coreelec.settings-local` | [pannal/service.coreelec.settings](https://github.com/pannal/service.coreelec.settings) |
| `projects/Amlogic-ce/packages/mediacenter/skin.p3i.estuary` | `sources/skin.p3i.estuary/skin.p3i.estuary-local` | bundled skin |

Clone each repo to the listed path (the branch to check out follows from `PKG_VERSION`/`PKG_GIT_BRANCH` in the respective `package.mk`), or edit the `PKG_URL` in those files to point at your own checkouts.

One-time note: the first build also bootstraps the rust toolchain (for `libdovi`), which adds roughly an hour of host compilation. Subsequent builds reuse it.

## Issues & Support

* [Issue-tracker](https://github.com/pannal/CoreELEC/issues)
* [Discussions](https://github.com/pannal/CoreELEC/discussions)
* **Discord:** [TRaSH-Guides #ugoos-mediaplayer and #support channels](https://trash-guides.info/discord)

## Links
As my Github profile isn't public at the moment, here are the significant source-code repository links for you, if you're interested:
* [Releases](https://github.com/pannal/CoreELEC/releases)
* [XBMC/Kodi base repository](https://github.com/pannal/xbmc/)
* [CoreELEC Settings Addon repository](https://github.com/pannal/service.coreelec.settings)
* [Linux Amlogic repository](https://github.com/pannal/linux-amlogic)
* [Amlogic media modules repository](https://github.com/pannal/media_modules-aml)

## Thanks
Massive thanks to all the testers, especially the ones from [TRaSH-Guides #ugoos-mediaplayer](https://trash-guides.info/discord), who initially spiked my interest in continuing Jamal's work by exposing potential long standing UI issues in Kodi.

Also massive thanks to (not limited to, in no particular order):
* CPM
* Jamal
* Team CoreELEC
* avdvplus, SamuriHL and others involved in the CoreELEC custom builds
* Team Kodi
* OSMC

## Donations

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/Z8Z8X6P9T)

## License

CoreELEC original code is released under [GPLv2](https://www.gnu.org/licenses/gpl-2.0.html).

## Copyright

As CoreELEC includes code from many upstream projects it includes many copyright owners. CoreELEC makes NO claim of copyright on any upstream code. Patches to upstream code have the same license as the upstream project, unless specified otherwise. For a complete copyright list please checkout the source code to examine license headers. Unless expressly stated otherwise all code submitted to the CoreELEC project (in any form) is licensed under [GPLv2](https://www.gnu.org/licenses/gpl-2.0.html). You are absolutely free to retain copyright. To retain copyright simply add a copyright header to each submitted code page. If you submit code that is not your own work it is your responsibility to place a header stating the copyright.
