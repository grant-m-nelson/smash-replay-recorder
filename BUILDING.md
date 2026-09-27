# Building

You only need this to change the app. To just use it, download the installer from the releases page.

## Requirements

- Windows 10/11 with Python 3.12 (including Tcl/Tk)
- [Inno Setup 6](https://jrsoftware.org/isinfo.php)
- WSL with an Ubuntu distribution (to build the controller helper image)

## Steps

1. Create a virtual environment and install dependencies:

   ```powershell
   py -3.12 -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt pyinstaller==6.16.0
   ```

2. Put the bundled inputs in `vendor\` (this folder is git-ignored):

   - `vendor\ffmpeg\ffmpeg.exe`: from an LGPL build such as [BtbN FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds/releases) (`ffmpeg-master-latest-win64-lgpl.zip`, the `bin\ffmpeg.exe` file).
   - `vendor\usbipd-win_5.3.0_x64.msi`: from [usbipd-win 5.3.0](https://github.com/dorssel/usbipd-win/releases/tag/v5.3.0). The build checks its SHA-256.
   - `vendor\smash-recorder-helper.tar.gz`: the controller helper image, built inside WSL:

     ```powershell
     wsl -d Ubuntu -u root -- bash packaging/build_helper_image.sh /mnt/c/path/to/repo/vendor/smash-recorder-helper.tar.gz
     ```

3. Build the installer:

   ```powershell
   packaging\build.ps1 -Python .venv\Scripts\python.exe
   ```

   The output is `dist\SmashReplayRecorder-Setup.exe`.

## Tests

```powershell
.venv\Scripts\python -m unittest discover -s recorder_app -p "test_*.py" -t .
```

`recorder_app/test_engine.py` runs the recording engine against a simulated Switch (counting, recording, pausing and resuming, merging new replays, older-version errors).

## Layout

- `recorder_app/`: the app (window, recording engine, screen recognition, OBS and controller connections)
- `recorder_app/helper/switch_bridge.py`: the emulated controller, run inside the WSL helper
- `packaging/service/`: the small Windows service that lends the Bluetooth adapter to the helper and gives it back
- `packaging/`: build scripts and the Inno Setup installer script
