"""The elevated helper's pipe protocol, with elevation and usbipd stubbed out."""
import threading
import unittest
from unittest import mock

from recorder_app import admin
from recorder_app.controller import ControllerError


class AdminHelperTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.served = None

        def fake_elevated(executable, arguments, timeout):
            pipe, key, parent = arguments[-3:]
            self.served = threading.Thread(target=admin.serve, args=(pipe, key, int(parent)), daemon=True)
            self.served.start()
            self.served.join()
            return 0

        def fake_run(arguments, timeout=900):
            self.calls.append(arguments[1:])
            return 0, ''

        patches = [mock.patch.object(admin, 'run_elevated', fake_elevated),
                   mock.patch.object(admin, '_run', fake_run),
                   mock.patch.object(admin, 'usbipd_path', lambda: 'usbipd'),
                   mock.patch('recorder_app.controller.find_radios',
                              lambda: [mock.Mock(bus_id='2-5', forced=True)])]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_bound_adapter_is_released_on_quit(self):
        helper = admin.AdminHelper()
        helper.call('bind', '2-5')
        helper.stop()
        self.served.join(5)
        self.assertIn(['bind', '--force', '--busid', '2-5'], self.calls)
        self.assertIn(['unbind', '--busid', '2-5'], self.calls)

    def test_unknown_command_is_refused(self):
        helper = admin.AdminHelper()
        with self.assertRaises(ControllerError):
            helper.call('format_disk')
        helper.stop()


if __name__ == '__main__':
    unittest.main()
