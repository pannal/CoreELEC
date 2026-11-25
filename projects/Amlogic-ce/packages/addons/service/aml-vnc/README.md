# Experimental decoded SDR video composition

**Include decoded video (experimental)** is disabled by default. On the
Amlogic-ng FBDEV backend it combines `/dev/amvideocap0` GE2D BGR readback with
Kodi's premultiplied linear GUI and sends the result through ordinary RFB.
Keyboard, pointer, authentication, port and GUI-only defaults are retained.
Existing addon settings-change handling restarts only the addon.

The existing `001`/`002` patches supply the same-UID Kodi GUI lease and desktop
resize handling. Composition waits for an established viewer and Kodi's
`R` readiness reply. An ordinary final disconnect releases capture resources
on the next loop. The existing bounded 10-second reconnect grace can retain
the GUI lease and capture resources after the final resize-disconnected
client, avoiding repeated GUI resizes while a legacy viewer reconnects.
Kodi must implement the `kodi.aml-vnc.fb0.v1` lease, including linear-GUI
readiness and restoration. The corrected p3i Kodi implementation already
provides this lifecycle; no additional Kodi backport belongs in this change.
Other backends are not qualified for composition.

GUI and decoded video are sampled independently, before HDMI. This is useful
for coarse SDR picture, subtitle, GUI and seek diagnostics, **not** AFBC-primary,
HDR/Dolby processing, exact colors/corruption, HDMI/HDCP, atomic scanout, frame
pacing or audio-sync validation. No decoder double-write setting is changed.
Compressed video, unsupported crop/offset/transforms, invalid geometry or
capture failures stop the service with status 66 instead of restarting
repeatedly. Disable the option to recover GUI-only operation.
The GUI limit is 1920x1080; higher-raster and 4K restoration/mapping cases have
unit coverage, not current hardware qualification.

## Source and dependency pairing

`003-hardware-video-composition.patch` applies after the unchanged lease
patches to the checksum-pinned dtechsrv/aml-vnc-server 1.4.0 archive. Upstream
AUTHORS and LGPL-3.0-or-later source notices are retained; the new compositor
and server tests use that license. Only ioctl command identifiers and scalar
ABI types are used: no vendor implementation/header, private helper, generated
header, binary, credential or machine-specific configuration is bundled.

The LibVNCServer 0.9.15 patch is GPL-2.0-or-later, consistent with the patched
upstream `sockets.c`; its original copyright/license notices remain intact.
It uses a monotonic deadline only for positive Linux write timeouts below
5000 ms. Composition temporarily selects 1000 ms around framebuffer writes
and restores the prior timeout, leaving reads and ordinary 20-second viewers
unchanged. The deadline covers each `rfbWriteExact`, not a whole multi-write
framebuffer update, capture or encoding.

Build the addon against the headers (including generated `rfbconfig.h`) and
library produced by the **same patched dependency recipe**. The existing
`libvncserver` target dependency and addon copy from its target build directory
preserve this pairing. Do not substitute a library from an installed addon or
copy a donor generated header. The `666` version prefix follows this Pannal
branch's existing addon convention; it is not an upstream release number.

## Validation and remaining qualification

The reviewed prototype passed three Linux ASan/UBSan suites for pixel/axis
composition, idle/resize lease gating and real blocked/trickling/fast socket
writes. Native 720x480 SDR playback covered GUI/video, subtitles, seek, input,
reconnect and option Off/On. A native nonreader disconnected in 1.133 seconds
while an ordinary 20-second viewer completed. These results belong to the
reviewed prototype, not a newly compiled SYSTEM or this addon revision.
Delivered video was only a few updates per second; this is not a smooth
20-FPS streaming service.

For a host-only replay, unpack the pinned archives into `source` and
`deps/source`, apply the addon patches in lexical order with `patch --fuzz=0
-p1`, and apply the dependency patch to `deps/source`. Configure LibVNCServer
for the host to generate its headers, then compile `source/tests/test_video.c`,
`test_server.c` and `test_write.c` with those headers, the dependency common
include directory, ASan/UBSan, function/data sections and linker
`--gc-sections`. The socket test intentionally includes the sibling
`deps/source/src/libvncserver/sockets.c` to exercise the actual patched code.
No target compiler or device is needed for these tests.

A fresh target addon build and hardware check against the current p3i Kodi
and SYSTEM are required before release, especially above the retained 480p
cohort. The server core is a candidate for dtechsrv/aml-vnc-server after that
qualification; the standalone short-write fix belongs with LibVNCServer and
can then be dropped from CoreELEC. Kodi already supplies the lease, and this
RFB addon does not require Jellyfin changes.
