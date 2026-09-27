"""Emulated Pro Controller bridge, run as root inside the WSL helper.

Protocol (JSON lines):
  stdin : {"cmd": "press", "id": 7, "buttons": ["x", "down"], "down": 0.15}
          {"cmd": "quit"}
  stdout: {"event": "state", "state": "connecting|connected|crashed", "switch": "AA:..", "error": ".."}
          {"event": "done", "id": 7}
          {"event": "log", "message": ".."}
Exits when stdin closes, so the controller disappears with the Windows app.
"""
import argparse
import importlib
import json
import os
import subprocess
import sys
import threading
import time

for candidate in ('/opt/smash-recorder/nxbt', '/opt/switch-nxbt'):
    if os.path.isdir(candidate):
        sys.path.insert(0, candidate)
        break
import nxbt  # noqa: E402

OUT_LOCK = threading.Lock()
DIRECTIONS = {name: 'DPAD_' + name.upper() for name in ('up', 'down', 'left', 'right')}
ALIASES = {'plus': 'PLUS', 'minus': 'MINUS', 'home': 'HOME', 'capture': 'CAPTURE'}


def emit(**event):
    with OUT_LOCK:
        sys.stdout.write(json.dumps(event) + '\n')
        sys.stdout.flush()


def clean_sdp_as_root():
    # NXBT would chmod the SDP socket world-writable; unnecessary as root.
    listing = subprocess.check_output(['sdptool', 'browse', 'local'], text=True)
    for record in listing.split('\n\n'):
        if 'PnP Information' in record:
            continue
        for line in record.splitlines():
            if 'Service RecHandle:' in line:
                subprocess.run(['sdptool', 'del', line.split()[-1]], check=True)


def forget_switches():
    """Pairing mode: drop stale Switch bonds; a stale key makes auth fail."""
    listing = subprocess.run(['bluetoothctl', 'devices'], capture_output=True, text=True).stdout
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == 'Device':
            subprocess.run(['bluetoothctl', 'remove', parts[1]], capture_output=True)
            emit(event='log', message='Removed previous pairing ' + parts[1])


def button_names(buttons):
    names = []
    for button in buttons:
        key = button.lower()
        name = DIRECTIONS.get(key) or ALIASES.get(key) or key.upper()
        if not hasattr(nxbt.Buttons, name):
            raise ValueError('Unknown button ' + button)
        names.append(getattr(nxbt.Buttons, name))
    return names


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--pair', action='store_true')
    mode.add_argument('--reconnect')
    args = parser.parse_args()

    bluez = importlib.import_module('nxbt.bluez')
    bluez.clean_sdp_records = clean_sdp_as_root
    # NXBT deletes bonds when a reconnect stalls; keep them so the saved
    # Switch can connect back.
    bluez.BlueZ.remove_device = lambda self, *a, **k: None
    if args.pair:
        forget_switches()
    controller = nxbt.Nxbt()
    index = controller.create_controller(nxbt.PRO_CONTROLLER, reconnect_address=None if args.pair else args.reconnect)

    def report():
        last = None
        while True:
            state = controller.state.get(index, {})
            current = (state.get('state'), state.get('last_connection'), state.get('errors'))
            if current != last:
                emit(event='state', state=current[0], switch=current[1], error=current[2] or None)
                last = current
            time.sleep(.25)
    threading.Thread(target=report, daemon=True).start()

    for line in sys.stdin:
        try:
            command = json.loads(line)
        except ValueError:
            emit(event='log', message='Ignored unreadable command')
            continue
        if command.get('cmd') == 'quit':
            break
        if command.get('cmd') != 'press':
            continue
        try:
            down = min(max(float(command.get('down', .15)), .05), 2.0)
            controller.press_buttons(index, button_names(command['buttons']), down=down, up=.15, block=False)
            # Nonblocking macros can overlap; pace them here instead.
            time.sleep(down + .2)
            emit(event='done', id=command.get('id'))
        except Exception as error:  # Keep the bridge alive for later inputs.
            emit(event='done', id=command.get('id'), error=str(error))
    try:
        controller.remove_controller(index)
    except Exception:
        pass


if __name__ == '__main__':
    main()
