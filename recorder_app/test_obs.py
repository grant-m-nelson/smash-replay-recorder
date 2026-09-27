import json
import unittest
from recorder_app.obs import ObsInspector, ObsError


class Socket:
    def __init__(self, auth=False, reject=False):
        hello = {'rpcVersion': 1}
        if auth: hello['authentication'] = {'salt': 'salt', 'challenge': 'challenge'}
        self.messages = [{'op': 0, 'd': hello}]
        self.sent, self.closed, self.reject = [], False, reject

    def recv(self): return json.dumps(self.messages.pop(0)) if self.messages else ''
    def settimeout(self, timeout): pass
    def close(self): self.closed = True

    def send(self, data):
        data = json.loads(data); self.sent.append(data)
        if data['op'] == 1:
            self.messages.append({'op': 2 if not self.reject else 9, 'd': {}})
        elif data['op'] == 6:
            request = data['d']
            results = {'GetVersion': {'obsVersion': '32.0.0'},
                'GetRecordStatus': {'outputActive': True},
                'GetStreamStatus': {'outputActive': False},
                'GetInputList': {'inputs': [{'inputName': 'Capture', 'inputKind': 'dshow_input'}]},
                'GetCurrentProgramScene': {'currentProgramSceneName': 'Live scene'},
                'GetVideoSettings': {'baseWidth': 1920, 'baseHeight': 1080},
                'GetSourceScreenshot': {'imageData': 'data:image/png;base64,bm90LXBuZw=='}}
            # Events and unrelated responses must not be mistaken for this request's result.
            self.messages.append({'op': 5, 'd': {'eventType': 'Ignored'}})
            self.messages.append({'op': 7, 'd': {'requestId': 'other'}})
            self.messages.append({'op': 7, 'd': {'requestId': request['requestId'],
                'requestStatus': {'result': True}, 'responseData': results[request['requestType']]}})


class ObsSetupTests(unittest.TestCase):
    def test_authenticated_busy_obs_is_read_only(self):
        socket = Socket(auth=True)
        connection = {}
        def connect(url, **kwargs):
            connection.update(url=url, **kwargs)
            return socket
        with ObsInspector(password='private-password', connect=connect) as obs:
            result = obs.inspect()
            self.assertTrue(result['recording'])
            with self.assertRaises(ObsError): obs.request('StopRecord')
        self.assertTrue(socket.closed)
        self.assertEqual(connection['url'], 'ws://127.0.0.1:4455')
        self.assertNotIn('private-password', json.dumps(socket.sent))
        self.assertTrue(all(x['d']['requestType'].startswith('Get') for x in socket.sent if x['op']==6))

    def test_missing_password_and_rejected_auth_close_socket(self):
        for password, reject in [('', False), ('private-password', True)]:
            socket = Socket(auth=True, reject=reject)
            with self.subTest(password=bool(password)), self.assertRaises(ObsError) as error:
                with ObsInspector(password=password, connect=lambda *a, **k: socket): pass
            self.assertNotIn('private-password', str(error.exception))
            self.assertTrue(socket.closed)

    def test_connection_error_does_not_echo_credentials(self):
        def connect(*args, **kwargs): raise OSError('secret server response private-password')
        with self.assertRaises(ObsError) as error:
            with ObsInspector(password='private-password', connect=connect): pass
        self.assertNotIn('private-password', str(error.exception))

    def test_malformed_preview_is_rejected(self):
        socket = Socket()
        with ObsInspector(connect=lambda *a, **k: socket) as obs:
            with self.assertRaises(ObsError): obs.preview('Capture')



if __name__ == '__main__': unittest.main()
