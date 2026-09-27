"""Handing the Bluetooth adapter to the recorder and back, without prompts.

The installer adds a small Windows service (packaging/service) that performs
the privileged usbipd steps. It releases the adapter itself when this app's
connection closes, so a crash still gives Bluetooth back to Windows. Without
the service (e.g. running from source) the one-prompt AdminHelper is used.
"""
import threading

from recorder_app.admin import AdminHelper
from recorder_app.controller import ControllerError

PIPE = r'\\.\pipe\SmashReplayRecorder'


class ServiceClient:
    def __init__(self):
        self.handle = open(PIPE, 'r+b', buffering=0)
        self._lock = threading.Lock()

    def call(self, command, bus_id=''):
        line = (command + (' ' + bus_id if bus_id else '') + '\n').encode()
        with self._lock:
            self.handle.write(line)
            reply = b''
            while not reply.endswith(b'\n'):
                chunk = self.handle.read(1)
                if not chunk:
                    raise ControllerError('The recorder\'s Bluetooth service stopped. Restart your PC and try again.')
                reply += chunk
        reply = reply.decode(errors='replace').strip()
        if reply != 'OK':
            raise ControllerError('Bluetooth could not be switched over: ' + reply.removeprefix('ERR ').strip())

    def close(self):
        try:
            self.handle.close()   # the service releases whatever this connection bound
        except OSError:
            pass


class BluetoothAccess:
    """bind()/release() through the service if installed, else via one UAC prompt."""

    def __init__(self):
        self.service = None
        self.admin = None
        self.bound = set()

    def _backend(self):
        if self.service or self.admin:
            return self.service or self.admin
        try:
            self.service = ServiceClient()
            self.service.call('PING')
        except OSError:
            self.service = None
            self.admin = AdminHelper()
        return self.service or self.admin

    @property
    def uses_service(self):
        self._backend()
        return self.service is not None

    def bind(self, bus_id):
        backend = self._backend()
        if self.service:
            backend.call('BIND', bus_id)
        else:
            backend.call('bind', bus_id)
        self.bound.add(bus_id)

    def release_all(self):
        for bus_id in list(self.bound):
            try:
                if self.service:
                    self.service.call('RELEASE', bus_id)
                elif self.admin:
                    self.admin.call('release', bus_id)
            except Exception:
                pass
            self.bound.discard(bus_id)

    def install(self, what):
        """usbipd / WSL installs when Setup was skipped: needs the admin helper."""
        if self.admin is None:
            self.admin = AdminHelper()
        return self.admin.call(what)

    def close(self):
        self.release_all()
        if self.service:
            self.service.close()
            self.service = None
        if self.admin:
            self.admin.stop()
            self.admin = None
