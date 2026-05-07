IMPORTANT: THIS IS A FORK OF p3i MADE BY pannal - https://github.com/pannal/CoreELEC/releases DO NOT BUILD OR PR AGAINST, THIS IS FOR PERSONAL DEV ONLY

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
* FFMPEG 8.0.1

## Issues & Support

* [Issue-tracker](https://github.com/samtheruby/CoreELEC/issues)
* [Discussions](https://github.com/samtheruby/CoreELEC/discussions)
* **Discord:** [TRaSH-Guides #ugoos-mediaplayer and #support channels](https://trash-guides.info/discord)

## Links
* [Releases](https://github.com/samtheruby/CoreELEC/releases)
* [XBMC/Kodi base repository](https://github.com/samtheruby/xbmc/)
* [CoreELEC Settings Addon repository](https://github.com/samtheruby/service.coreelec.settings)
* [Linux Amlogic repository](https://github.com/samtheruby/linux-amlogic)
* [Amlogic media modules repository](https://github.com/samtheruby/media_modules-aml)

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
