# PES6Builder notices

`PES6Builder.exe` is an original setup and build orchestration program. It
contains no PES6 code, game data, generated translation units, or compiled game.

The prebuilt executable embeds the following components:

| Component | License | Project |
| --- | --- | --- |
| Python | PSF License 2.0 | https://www.python.org/ |
| Tcl/Tk | BSD-style | https://www.tcl.tk/ |
| Pillow | HPND | https://python-pillow.org/ |
| PyInstaller | GPL-2.0-or-later with bootloader exception | https://pyinstaller.org/ |

The packaged builder includes the public project source and downloads the pinned
ReXGlue SDK at runtime. Those components keep their own licenses. See
`THIRD_PARTY_NOTICES.md` for the ReXGlue and SDK dependency notices.
