"""Connects the engine to the real OBS and controller."""
import shutil
import time

from recorder_app.obs import ObsError, ObsRecorder


class LiveIO:
    """Engine I/O over one OBS connection (engine thread only) and the controller."""

    def __init__(self, port, password, controller, output_dir, source=None):
        self.port, self.password = port, password
        self.controller = controller
        self.output_dir = output_dir
        self.obs = None
        self.source = source
        self._connect()

    def _connect(self):
        if self.obs:
            self.obs.close()
        self.obs = ObsRecorder(self.port, self.password, timeout=10).__enter__()
        if not self.source:
            self.source = self.obs.scene()

    def _call(self, function):
        """One reconnect attempt: OBS occasionally drops idle WebSocket clients."""
        try:
            return function()
        except ObsError:
            time.sleep(1)
            self._connect()
            return function()

    def frame(self):
        return self._call(lambda: self.obs.frame(self.source))

    def press(self, *buttons, down=.15, wait=True):
        self.controller.press(*buttons, down=down, wait=wait)

    def controller_ok(self):
        return self.controller.connected

    def record_start(self):
        self._call(lambda: self.obs.request('StartRecord'))

    def record_stop(self):
        return self._call(lambda: self.obs.request('StopRecord'))['outputPath']

    def recording(self):
        return self._call(lambda: self.obs.request('GetRecordStatus'))['outputActive']

    def free_bytes(self):
        return shutil.disk_usage(self.output_dir).free

    sleep = staticmethod(time.sleep)
    now = staticmethod(time.monotonic)

    def close(self):
        if self.obs:
            self.obs.close()
