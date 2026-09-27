"""What this PC still needs for controller emulation, and the non-admin install step.

Admin-only pieces (usbipd, WSL itself) are installed by recorder_app.admin.
Importing the helper image into WSL runs as the normal user.
"""
import os
from pathlib import Path
import subprocess

from recorder_app.admin import bundled
from recorder_app.controller import NO_WINDOW, ControllerError, usbipd_path

HELPER_DISTRO = 'SmashRecorder'
HELPER_IMAGE = 'smash-recorder-helper.tar.gz'


def _run(arguments, timeout=120):
    try:
        result = subprocess.run(arguments, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return 1, ''
    raw = result.stdout + result.stderr
    return result.returncode, raw.decode('utf-16-le' if b'\x00' in raw else 'utf-8', errors='replace')


def wsl_installed():
    code, output = _run(['wsl.exe', '--version'], timeout=60)
    return code == 0 and 'WSL' in output


def usbipd_installed():
    try:
        usbipd_path()
        return True
    except ControllerError:
        return False


def distributions():
    code, output = _run(['wsl.exe', '--list', '--quiet'], timeout=60)
    return [line.strip() for line in output.splitlines() if line.strip()] if code == 0 else []


def check(distribution=HELPER_DISTRO):
    wsl = wsl_installed()
    return {'wsl': wsl, 'usbipd': usbipd_installed(),
            'helper': wsl and distribution in distributions()}


def install_location():
    return Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'SmashReplayRecorder' / 'wsl'


def import_helper(distribution=HELPER_DISTRO):
    image = bundled(HELPER_IMAGE)
    if not image.is_file():
        raise ControllerError('The controller helper is missing from this installation. Reinstall the recorder.')
    location = install_location()
    location.mkdir(parents=True, exist_ok=True)
    code, output = _run(['wsl.exe', '--import', distribution, str(location), str(image), '--version', '2'],
                        timeout=1800)
    if code:
        if 'virtual' in output.lower() or 'hypervisor' in output.lower():
            raise ControllerError('Windows needs virtualization turned on to run the controller helper. '
                                  'Enable "Virtualization" (Intel VT-x / AMD SVM) in your PC\'s BIOS settings, then try again.')
        raise ControllerError('Installing the controller helper failed. Restart your PC and try again.')
    code, _ = _run(['wsl.exe', '-d', distribution, '-u', 'root', '--', 'test', '-d', '/opt/smash-recorder/nxbt'],
                   timeout=300)
    if code:
        raise ControllerError('The controller helper did not start correctly. Restart your PC and try again.')


def remove_helper(distribution=HELPER_DISTRO):
    """Uninstall: remove the helper distribution (the app's own, never a user's)."""
    if distribution != HELPER_DISTRO:
        raise ValueError('Only the recorder\'s own helper can be removed.')
    _run(['wsl.exe', '--unregister', distribution], timeout=300)
