"""Windows side of the emulated controller: radio hand-off plus the WSL bridge.

The Bluetooth radio is shared with WSL through usbipd. Windows' own Bluetooth
service keeps the radio open, so the first hand-off needs a forced bind, which
is the only step requiring administrator approval (one UAC prompt).
"""
import ctypes
from dataclasses import dataclass
import itertools
import json
import os
from pathlib import Path, PureWindowsPath
import queue
import shutil
import subprocess
import sys
import threading
import time

NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


class ControllerError(Exception):
    """Plain-language problem the UI can show directly."""


@dataclass(frozen=True)
class Radio:
    bus_id: str
    description: str
    instance_id: str
    forced: bool
    attached: bool


def usbipd_path():
    found = shutil.which('usbipd.exe') or shutil.which('usbipd')
    default = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'usbipd-win/usbipd.exe'
    if found:
        return found
    if default.is_file():
        return str(default)
    raise ControllerError('The USB sharing helper (usbipd) is not installed.')


def _run(arguments, timeout=30):
    result = subprocess.run(arguments, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    raw = result.stdout + result.stderr
    encoding = 'utf-16-le' if b'\x00' in raw else 'utf-8'
    return result.returncode, raw.decode(encoding, errors='replace').strip()


def find_radios():
    code, output = _run([usbipd_path(), 'state'])
    if code:
        raise ControllerError('Could not list USB devices. Restart the computer and try again.')
    devices = json.loads(output).get('Devices', [])
    radios = []
    for device in devices:
        if 'bluetooth' in (device.get('Description') or '').lower() and device.get('BusId'):
            radios.append(Radio(device['BusId'], device['Description'], device.get('InstanceId') or '',
                                bool(device.get('IsForced')), bool(device.get('ClientIPAddress'))))
    return radios


def choose_radio(preferred_instance=''):
    radios = find_radios()
    if not radios:
        raise ControllerError('No Bluetooth adapter was found. Plug in or enable a Bluetooth adapter.')
    for radio in radios:
        if preferred_instance and radio.instance_id == preferred_instance:
            return radio
    return radios[0]


def run_elevated(executable, arguments, timeout=180):
    """ShellExecute 'runas' and wait. Returns the exit code, or raises if declined."""
    SEE_MASK_NOCLOSEPROCESS = 0x40

    class SHELLEXECUTEINFO(ctypes.Structure):
        _fields_ = [('cbSize', ctypes.c_ulong), ('fMask', ctypes.c_ulong), ('hwnd', ctypes.c_void_p),
                    ('lpVerb', ctypes.c_wchar_p), ('lpFile', ctypes.c_wchar_p),
                    ('lpParameters', ctypes.c_wchar_p), ('lpDirectory', ctypes.c_wchar_p),
                    ('nShow', ctypes.c_int), ('hInstApp', ctypes.c_void_p), ('lpIDList', ctypes.c_void_p),
                    ('lpClass', ctypes.c_wchar_p), ('hkeyClass', ctypes.c_void_p),
                    ('dwHotKey', ctypes.c_ulong), ('hIcon', ctypes.c_void_p), ('hProcess', ctypes.c_void_p)]
    info = SHELLEXECUTEINFO(cbSize=ctypes.sizeof(SHELLEXECUTEINFO), fMask=SEE_MASK_NOCLOSEPROCESS,
                            lpVerb='runas', lpFile=executable, lpParameters=subprocess.list2cmdline(arguments),
                            nShow=0)
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        raise ControllerError('Windows permission was not granted. Click "Yes" on the Windows prompt to continue.')
    kernel32 = ctypes.windll.kernel32
    kernel32.WaitForSingleObject(ctypes.c_void_p(info.hProcess), int(timeout * 1000))
    code = ctypes.c_ulong()
    kernel32.GetExitCodeProcess(ctypes.c_void_p(info.hProcess), ctypes.byref(code))
    kernel32.CloseHandle(ctypes.c_void_p(info.hProcess))
    return code.value


def attach_radio(radio, distribution):
    """Connect an already force-bound adapter to WSL (no admin rights needed)."""
    if radio.attached:
        return radio
    # Start the helper VM first; usbipd attaches to the running WSL instance.
    _run(['wsl.exe', '-d', distribution, '-u', 'root', '--', 'true'], timeout=120)
    code, _ = _run([usbipd_path(), 'attach', '--wsl', '--busid', radio.bus_id], timeout=60)
    if code:
        raise ControllerError('The Bluetooth adapter could not be connected to the controller helper. '
                              'Unplug and replug the adapter (or restart), then try again.')
    return choose_radio(radio.instance_id)


def wsl_path(path):
    windows = PureWindowsPath(Path(path).resolve())
    return '/mnt/' + windows.drive[0].lower() + '/' + '/'.join(windows.parts[1:])


def helper_script():
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    return base / 'recorder_app/helper/switch_bridge.py'


class Controller:
    """One emulated Pro Controller. Thread-safe press(); state from the bridge."""

    def __init__(self, distribution, switch_address='', on_event=None):
        self.distribution = distribution
        self.switch_address = switch_address
        self.on_event = on_event or (lambda event: None)
        self.state = 'stopped'
        self.error = None
        self.process = None
        self._ids = itertools.count(1)
        self._done = {}
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)

    @property
    def connected(self):
        return self.state == 'connected' and self.process is not None and self.process.poll() is None

    def start(self, pair):
        self.stop()
        if not pair and not self.switch_address:
            raise ControllerError('No Switch has been paired yet.')
        mode = ['--pair'] if pair else ['--reconnect', self.switch_address]
        self.state, self.error = 'starting', None
        self.process = subprocess.Popen(
            ['wsl.exe', '-d', self.distribution, '-u', 'root', '--', 'python3', '-u',
             wsl_path(helper_script()), *mode],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=NO_WINDOW, text=True, encoding='utf-8', bufsize=1)
        threading.Thread(target=self._read, args=(self.process,), daemon=True).start()
        threading.Thread(target=self._drain_errors, args=(self.process,), daemon=True).start()

    def _drain_errors(self, process):
        lines = []
        for line in process.stderr:
            lines.append(line.rstrip())
        if lines and self.state not in ('connected',):
            self.error = lines[-1][-300:]

    def _read(self, process):
        for line in process.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            with self._lock:
                if event.get('event') == 'state':
                    self.state = event.get('state') or 'connecting'
                    if event.get('switch'):
                        self.switch_address = event['switch']
                    if event.get('error'):
                        self.error = str(event['error'])[-300:]
                elif event.get('event') == 'done':
                    self._done[event.get('id')] = event.get('error')
                self._changed.notify_all()
            self.on_event(event)
        with self._lock:
            if self.process is process:
                self.state = 'stopped'
            self._changed.notify_all()
        self.on_event({'event': 'state', 'state': 'stopped'})

    def wait_connected(self, timeout, stable=4.0):
        """Connected continuously for `stable` seconds (the first link often flaps once)."""
        deadline = time.monotonic() + timeout
        since = None
        with self._lock:
            while True:
                now = time.monotonic()
                if self.connected:
                    since = since or now
                    if now - since >= stable:
                        return True
                else:
                    since = None
                    if self.state in ('crashed', 'stopped'):
                        return False
                if now > deadline:
                    return False
                self._changed.wait(.25)

    def press(self, *buttons, down=.15, wait=True):
        if not self.connected and not self.wait_connected(10, stable=0):
            raise ControllerError('The controller is not connected to the Switch.')
        command_id = next(self._ids)
        with self._lock:
            self.process.stdin.write(json.dumps({'cmd': 'press', 'id': command_id,
                                                 'buttons': list(buttons), 'down': down}) + '\n')
            self.process.stdin.flush()
            if not wait:
                return
            deadline = time.monotonic() + down + 5
            while command_id not in self._done:
                if time.monotonic() > deadline or self.process is None or self.process.poll() is not None:
                    raise ControllerError('The Switch stopped responding to the controller.')
                self._changed.wait(.2)
            error = self._done.pop(command_id)
        if error:
            raise ControllerError('A button press failed: ' + error)

    def stop(self):
        process, self.process = self.process, None
        if process is None:
            return
        try:
            process.stdin.write(json.dumps({'cmd': 'quit'}) + '\n')
            process.stdin.close()
            process.wait(8)
        except Exception:
            process.kill()
        self.state = 'stopped'

