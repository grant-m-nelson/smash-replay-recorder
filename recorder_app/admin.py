"""Elevated helper: the only code that runs as administrator.

The app starts it once per session through the normal Windows permission
prompt. It accepts a handful of fixed commands over an authenticated local
pipe, and when the app exits (or crashes) it gives the Bluetooth adapter back
to Windows before quitting.

Commands: install_usbipd, install_wsl, bind (busid), release (busid), quit.
"""
from multiprocessing.connection import Client, Listener
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
import uuid

from recorder_app.controller import NO_WINDOW, ControllerError, run_elevated, usbipd_path

USBIPD_MSI = 'usbipd-win_5.3.0_x64.msi'
USBIPD_SHA256 = '1c984914aec944de19b64eff232421439629699f8138e3ddc29301175bc6d938'


def bundled(name):
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    return base / 'vendor' / name


def _sha256(path):
    import hashlib
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _run(arguments, timeout=900):
    result = subprocess.run(arguments, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    raw = result.stdout + result.stderr
    return result.returncode, raw.decode('utf-16-le' if b'\x00' in raw else 'utf-8', errors='replace')


# ---- elevated side --------------------------------------------------------
class Worker:
    def __init__(self):
        self.bound = set()   # bus IDs this session force-bound

    def install_usbipd(self):
        msi = bundled(USBIPD_MSI)
        if not msi.is_file() or _sha256(msi) != USBIPD_SHA256:
            raise ControllerError('The bundled USB sharing installer is missing or damaged. Reinstall the recorder.')
        code, _ = _run(['msiexec.exe', '/i', str(msi), '/qn', '/norestart'])
        if code not in (0, 3010):
            raise ControllerError(f'Installing the USB sharing helper failed (code {code}).')
        return {'reboot': code == 3010}

    def install_wsl(self):
        code, output = _run(['wsl.exe', '--install', '--no-distribution'], timeout=1800)
        if code not in (0, 3010):
            raise ControllerError('Installing the Windows Subsystem for Linux failed. Make sure Windows Update '
                                  'works and virtualization is enabled, then try again.')
        needs = code == 3010 or 'restart' in output.lower() or 'reboot' in output.lower()
        return {'reboot': needs}

    def bind(self, bus_id):
        """Force-bind; also adopts an adapter left bound by an earlier crash so it gets released."""
        from recorder_app.controller import find_radios
        code, _ = _run([usbipd_path(), 'bind', '--force', '--busid', bus_id], timeout=60)
        if not any(r.bus_id == bus_id and r.forced for r in find_radios()):
            raise ControllerError('Windows would not hand the Bluetooth adapter to the recorder.')
        self.bound.add(bus_id)
        return {}

    def release(self, bus_id):
        usbipd = usbipd_path()
        _run([usbipd, 'detach', '--busid', bus_id], timeout=60)
        code, _ = _run([usbipd, 'unbind', '--busid', bus_id], timeout=60)
        self.bound.discard(bus_id)
        return {'ok': code == 0}

    def release_all(self):
        for bus_id in list(self.bound):
            try:
                self.release(bus_id)
            except Exception:
                pass


def serve(pipe, key_hex, parent_pid):
    """Entry point for the elevated process."""
    worker = Worker()
    conn = Client(pipe, authkey=bytes.fromhex(key_hex))
    stop = threading.Event()

    def watch_parent():
        import ctypes
        SYNCHRONIZE = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, parent_pid)
        if handle:
            ctypes.windll.kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
        stop.set()
        worker.release_all()
        os._exit(0)
    threading.Thread(target=watch_parent, daemon=True).start()

    try:
        while not stop.is_set():
            try:
                request = conn.recv()
            except (EOFError, OSError):
                break
            command, args = request.get('cmd'), request.get('args', [])
            if command == 'quit':
                break
            handler = {'install_usbipd': worker.install_usbipd, 'install_wsl': worker.install_wsl,
                       'bind': worker.bind, 'release': worker.release}.get(command)
            try:
                if handler is None:
                    raise ControllerError('Unknown request.')
                conn.send({'ok': True, 'result': handler(*args)})
            except Exception as error:  # noqa: BLE001 - reported to the app
                conn.send({'ok': False, 'error': str(error) if isinstance(error, ControllerError)
                           else 'An administrator step failed.'})
    finally:
        worker.release_all()


# ---- app side --------------------------------------------------------------
class AdminHelper:
    """Starts the elevated helper (one permission prompt) and sends it commands."""

    def __init__(self):
        self.conn = None
        self._lock = threading.Lock()

    @property
    def running(self):
        return self.conn is not None

    def start(self, timeout=120):
        if self.conn:
            return
        pipe = r'\\.\pipe\smash-recorder-' + uuid.uuid4().hex
        key = secrets.token_bytes(32)
        listener = Listener(pipe, authkey=key)
        if getattr(sys, 'frozen', False):
            executable, prefix = sys.executable, []
        else:
            executable, prefix = sys.executable, ['-m', 'recorder_app']
        arguments = prefix + ['--admin-helper', pipe, key.hex(), str(os.getpid())]
        accepted = {}

        def accept():
            try:
                accepted['conn'] = listener.accept()
            except Exception as error:  # noqa: BLE001
                accepted['error'] = error
        thread = threading.Thread(target=accept, daemon=True)
        thread.start()
        launched = {}

        def launch():
            try:
                launched['code'] = run_elevated(executable, arguments, timeout=24 * 3600)
            except ControllerError as error:
                launched['error'] = error
        threading.Thread(target=launch, daemon=True).start()
        deadline = time.monotonic() + timeout
        while 'conn' not in accepted and time.monotonic() < deadline:
            if 'error' in launched:
                listener.close()
                raise launched['error']
            time.sleep(.1)
        if 'conn' not in accepted:
            listener.close()
            raise ControllerError('Windows permission was not granted. Click "Yes" on the Windows prompt.')
        self.conn = accepted['conn']

    def call(self, command, *args):
        self.start()
        with self._lock:
            self.conn.send({'cmd': command, 'args': list(args)})
            reply = self.conn.recv()
        if not reply.get('ok'):
            raise ControllerError(reply.get('error') or 'An administrator step failed.')
        return reply.get('result') or {}

    def stop(self):
        if self.conn:
            try:
                self.conn.send({'cmd': 'quit'})
            except Exception:
                pass
            self.conn.close()
            self.conn = None
