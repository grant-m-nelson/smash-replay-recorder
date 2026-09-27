"""Local OBS setup inspection. This client cannot issue mutating requests."""
import base64
import hashlib
import json
import time
import uuid

import websocket


class ObsError(Exception):
    """A message safe to display without leaking server responses or credentials."""


READ_REQUESTS = frozenset({
    'GetVersion', 'GetRecordStatus', 'GetStreamStatus', 'GetInputList',
    'GetCurrentProgramScene', 'GetSourceScreenshot', 'GetInputMute',
    'GetVideoSettings', 'GetSceneItemList',
})


class ObsInspector:
    allowed = READ_REQUESTS

    def __init__(self, port=4455, password='', timeout=8, connect=None):
        if type(port) is not int or not 1 <= port <= 65535:
            raise ObsError('Use the server port shown in OBS, between 1 and 65535.')
        self.port, self.password, self.timeout = port, password, timeout
        self.connect = connect or websocket.create_connection
        self.ws = None

    def __enter__(self):
        try:
            # Always loopback; never forward passwords to an arbitrary host or proxy.
            self.ws = self.connect(f'ws://127.0.0.1:{self.port}', timeout=self.timeout,
                                   http_no_proxy=['127.0.0.1', 'localhost'])
            hello = self._receive()
            if hello.get('op') != 0 or hello.get('d', {}).get('rpcVersion', 0) < 1:
                raise ObsError('This server does not support the OBS connection protocol. Update OBS.')
            identify = {'rpcVersion': 1, 'eventSubscriptions': 0}
            auth = hello['d'].get('authentication')
            if auth:
                if not self.password:
                    raise ObsError('Enter the server password shown in OBS > Tools > WebSocket Server Settings.')
                def digest(value):
                    return base64.b64encode(hashlib.sha256(value.encode()).digest()).decode()
                identify['authentication'] = digest(digest(self.password + auth['salt']) + auth['challenge'])
            self.ws.send(json.dumps({'op': 1, 'd': identify}))
            if self._receive().get('op') != 2:
                raise ObsError('OBS did not accept the connection. Check the server password and retry.')
            return self
        except ObsError:
            self.close()
            raise
        except Exception:
            self.close()
            raise ObsError('Could not connect to OBS. Open OBS, enable its WebSocket server, and check the port and password.') from None

    def _receive(self):
        data = self.ws.recv()
        if not data:
            raise ObsError('OBS closed the connection. Check the server password and retry.')
        return json.loads(data)

    def request(self, kind, data=None):
        if kind not in self.allowed:
            raise ObsError('Setup inspection cannot change OBS or start or stop a recording.')
        if self.ws is None:
            raise ObsError('Connect to OBS first.')
        request_id = uuid.uuid4().hex
        deadline = time.monotonic() + self.timeout
        try:
            self.ws.send(json.dumps({'op': 6, 'd': {'requestType': kind,
                'requestId': request_id, 'requestData': data or {}}}))
            while time.monotonic() < deadline:
                self.ws.settimeout(max(.05, deadline - time.monotonic()))
                message = self._receive()
                if message.get('op') != 7 or message.get('d', {}).get('requestId') != request_id:
                    continue
                response = message['d']
                if not response.get('requestStatus', {}).get('result'):
                    raise ObsError(f'OBS could not complete {kind}. Refresh the source list and retry.')
                return response.get('responseData', {})
            raise ObsError('OBS took too long to reply. Retry the connection.')
        except ObsError:
            raise
        except Exception:
            raise ObsError('The OBS connection was interrupted. Check that OBS is open and retry.') from None

    def inspect(self):
        version = self.request('GetVersion')
        recording = self.request('GetRecordStatus')['outputActive']
        streaming = self.request('GetStreamStatus')['outputActive']
        inputs = self.request('GetInputList')['inputs']
        scene = self.request('GetCurrentProgramScene')['currentProgramSceneName']
        video = self.request('GetVideoSettings')
        return {'version': version['obsVersion'], 'recording': recording,
                'streaming': streaming, 'scene': scene, 'inputs': inputs, 'video': video}

    def preview(self, source):
        if not source:
            raise ObsError('Choose the capture source showing your Switch.')
        response = self.request('GetSourceScreenshot', {'sourceName': source,
            'imageFormat': 'png', 'imageWidth': 640, 'imageHeight': 360})
        data = response.get('imageData', '')
        if not data.startswith('data:image/png;base64,') or len(data) > 4_000_000:
            raise ObsError('OBS returned an unreadable preview. Check the selected source.')
        try:
            decoded = base64.b64decode(data.split(',', 1)[1], validate=True)
            if not decoded.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError()
            return decoded
        except ValueError:
            raise ObsError('OBS returned an unreadable preview. Check the selected source.') from None

    def close(self):
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None

    def __exit__(self, *args):
        self.close()


RECORD_REQUESTS = READ_REQUESTS | {'StartRecord', 'StopRecord', 'GetStats', 'GetRecordDirectory'}


class ObsRecorder(ObsInspector):
    """Adds only recording start/stop; never changes scenes, sources or settings."""

    allowed = RECORD_REQUESTS

    def scene(self):
        return self.request('GetCurrentProgramScene')['currentProgramSceneName']

    def frame(self, source, width=960, height=540):
        """Current picture as a PIL image (JPEG keeps the round trip fast)."""
        from io import BytesIO
        from PIL import Image
        response = self.request('GetSourceScreenshot', {'sourceName': source, 'imageFormat': 'jpg',
                                                        'imageCompressionQuality': 85,
                                                        'imageWidth': width, 'imageHeight': height})
        data = response.get('imageData', '')
        try:
            return Image.open(BytesIO(base64.b64decode(data.split(',', 1)[1]))).convert('RGB')
        except Exception:
            raise ObsError('OBS returned an unreadable picture. Check that OBS shows your Switch.') from None


def local_settings(appdata=None):
    """(enabled, port, password) from this PC's OBS WebSocket settings, or None.

    Reading OBS's own config spares the user from copying a password. The
    password stays in memory only.
    """
    import os
    from pathlib import Path
    base = Path(appdata or os.environ.get('APPDATA', ''))
    path = base / 'obs-studio/plugin_config/obs-websocket/config.json'
    try:
        values = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    port = values.get('server_port', 4455)
    password = values.get('server_password', '') if values.get('auth_required', True) else ''
    return bool(values.get('server_enabled')), int(port), password
