# Third-party software

The installer (`SmashReplayRecorder-Setup.exe`) bundles the following unmodified third-party software. Each remains under its own license.

| Component | Version | License | Source |
|---|---|---|---|
| FFmpeg (`ffmpeg.exe`, BtbN LGPL build) | N-126889-gb139ba11d8-20260926 | LGPL v3 | [FFmpeg source at b139ba11d8](https://github.com/FFmpeg/FFmpeg/tree/b139ba11d8), [build scripts](https://github.com/BtbN/FFmpeg-Builds) |
| usbipd-win (installer, run only if missing) | 5.3.0 | GPL-3.0 | [dorssel/usbipd-win](https://github.com/dorssel/usbipd-win/tree/v5.3.0) |
| NXBT (in the controller helper) | commit ec4b800 | MIT | [Brikwerk/nxbt](https://github.com/Brikwerk/nxbt/tree/ec4b800ad6c55de96bb6c7f9f84b5bdc59a4c975) |
| Ubuntu 22.04 packages in the controller helper (BlueZ, systemd, Python 3, dbus-python, psutil and their dependencies) | jammy | Various (mostly GPL/LGPL) | `apt-get source <package>`, or [Launchpad](https://launchpad.net/ubuntu/jammy) |
| Python | 3.12 | PSF License | [python.org](https://www.python.org/downloads/source/) |
| Tcl/Tk | 8.6 | Tcl/Tk License (BSD-style) | [tcl.tk](https://www.tcl.tk/software/tcltk/) |
| Pillow | 12.3.0 | MIT-CMU | [python-pillow/Pillow](https://github.com/python-pillow/Pillow) |
| NumPy | 2.3.5 | BSD-3-Clause | [numpy/numpy](https://github.com/numpy/numpy) |
| websocket-client | 1.8.0 | Apache-2.0 | [websocket-client/websocket-client](https://github.com/websocket-client/websocket-client) |
| PyInstaller bootloader | 6.16.0 | GPL-2.0 with bootloader exception | [pyinstaller/pyinstaller](https://github.com/pyinstaller/pyinstaller) |

The installer itself is built with [Inno Setup](https://jrsoftware.org/isinfo.php).

The controller helper image is built from public Ubuntu packages and NXBT by [`packaging/build_helper_image.sh`](packaging/build_helper_image.sh); running that script reproduces it.

Screen-recognition templates (`recorder_app/assets/screens.npz`) are small grayscale crops of Nintendo Switch and Super Smash Bros. Ultimate interface text, used only to recognize which screen is showing. Nintendo owns those works.
