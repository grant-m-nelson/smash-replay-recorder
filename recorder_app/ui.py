"""Smash Replay Recorder window: a guided, mostly automatic flow.

Welcome → OBS → Controller → Replays → Record. Visual language follows the
StartSeeder web client (see theme.py). All device work runs on worker
threads; they report back through a queue polled by the Tk loop.
"""
from dataclasses import replace
import ctypes
import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox

from PIL import ImageTk

from recorder_app import screens, setup_env, theme
from recorder_app.bluetooth import BluetoothAccess
from recorder_app.config import RecorderConfig
from recorder_app.controller import Controller, ControllerError, attach_radio, choose_radio
from recorder_app.engine import Engine, Journal, NeedsAttention, StopRequested
from recorder_app.live import LiveIO
from recorder_app.obs import ObsError, ObsRecorder, local_settings

DEFAULT_DISTRO = setup_env.HELPER_DISTRO
SESSION_CHOICES = (('Until done', None), ('1 hour', 3600), ('2 hours', 7200),
                   ('4 hours', 14400), ('8 hours', 28800))
COLUMN = 680   # content width in design pixels, like StartSeeder's centered column


def duration_text(seconds):
    minutes = max(1, int(round(seconds / 60)))
    hours, minutes = divmod(minutes, 60)
    if not hours:
        return f'{minutes} min'
    return f'{hours} h {minutes} min' if minutes else f'{hours} h'


def clock_text(seconds_from_now):
    return time.strftime('%I:%M %p', time.localtime(time.time() + seconds_from_now)).lstrip('0')


def keep_awake(on):
    """Stop Windows sleeping during a recording run (released afterwards)."""
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
    except Exception:
        pass


class App(tk.Tk):
    STEPS = ('Welcome', 'OBS', 'Controller', 'Replays', 'Record')

    def __init__(self, state_directory):
        super().__init__()
        self.title('Smash Replay Recorder')
        self.scale = self.winfo_fpixels('1i') / 96.0
        self.c = theme.palette()
        self.geometry(f'{self.px(900)}x{self.px(780)}')
        self.minsize(self.px(760), self.px(700))
        self.configure(bg=self.c['page'])
        theme.match_title_bar(self, self.c['dark'])
        self.state_directory = Path(state_directory)
        self.settings_path = self.state_directory / 'settings.json'
        try:
            self.settings = RecorderConfig.load(self.settings_path)
        except Exception:
            self.settings = RecorderConfig(str(Path.home() / 'Videos' / 'Smash Replays'),
                                           controller_environment=DEFAULT_DISTRO)
        self.events = queue.Queue()
        self.obs_credentials = None
        self.controller = None
        self.radio = None
        self.bluetooth = BluetoothAccess()
        self.engine_thread = None
        self.engine = None
        self.io = None
        self.preview_image = None
        self.preview_running = False
        self.closed = False
        self.returning = False
        self.session_seconds = None
        self.session_started = time.monotonic()
        self.session_count = 0
        self.status_label = None
        self._layout()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.show_welcome()
        self.after(100, self.poll)

    def px(self, value):
        """Design pixels (96 DPI) to screen pixels."""
        return int(value * self.scale)

    # ---- building blocks ---------------------------------------------------
    def _layout(self):
        c = self.c
        nav = tk.Frame(self, bg=c['nav'], height=self.px(60))
        nav.pack(fill='x')
        nav.pack_propagate(False)
        brand = tk.Frame(nav, bg=c['nav'])
        brand.pack(side='left', padx=(self.px(28), 0))
        theme.logo(brand, c, self.scale, size=26).pack(side='left', padx=(0, self.px(10)))
        tk.Label(brand, text='Replay Recorder', font=(theme.FONT, 14, 'bold'), bg=c['nav'], fg=c['ink']).pack(side='left')
        steps = tk.Frame(nav, bg=c['nav'])
        steps.pack(side='right', padx=(0, self.px(28)))
        self.step_labels = []
        for name in self.STEPS:
            label = tk.Label(steps, text=name, font=(theme.FONT, 10), bg=c['nav'], fg=c['muted'])
            label.pack(side='left', padx=self.px(9))
            self.step_labels.append(label)
        tk.Frame(self, bg=c['nav_line'], height=1).pack(fill='x')
        self.page = tk.Frame(self, bg=c['page'])
        self.page.pack(fill='both', expand=True)
        self.column = tk.Frame(self.page, bg=c['page'])
        self.column.place(relx=.5, y=self.px(28), anchor='n', width=self.px(COLUMN), relheight=1, height=-self.px(28))

    def clear(self, step):
        self.preview_running = False
        self.status_label = None
        for index, label in enumerate(self.step_labels):
            label.configure(fg=self.c['ink'] if index == step else self.c['muted'],
                            font=(theme.FONT, 10, 'bold' if index == step else 'normal'))
        for child in self.column.winfo_children():
            child.destroy()

    def card(self, padding=22):
        card = theme.Card(self.column, self.c, self.scale, padding=padding)
        card.pack(fill='x', pady=(0, self.px(16)))
        return card.inner

    def label(self, parent, text, size=11, weight='normal', tone='ink', pady=(0, 0), wrap=True):
        widget = tk.Label(parent, text=text, font=(theme.FONT, size, weight), bg=parent['bg'], fg=self.c[tone],
                          justify='left', anchor='w', wraplength=self.px(COLUMN - 50) if wrap else 0)
        widget.pack(anchor='w', fill='x', pady=pady)
        return widget

    def heading(self, parent, text):
        return self.label(parent, text, size=16, weight='bold', pady=(0, self.px(4)))

    def stats(self, parent, *pairs, pady=(0, 0)):
        """StartSeeder stat row: big bold numbers with small muted units."""
        row = tk.Frame(parent, bg=parent['bg'])
        row.pack(anchor='w', pady=pady)
        for index, (big, unit) in enumerate(pairs):
            tk.Label(row, text=big, font=(theme.FONT, 24, 'bold'), bg=row['bg'], fg=self.c['ink']).pack(
                side='left', padx=(self.px(22) if index else 0, 0))
            tk.Label(row, text=unit, font=(theme.FONT, 11), bg=row['bg'], fg=self.c['muted']).pack(
                side='left', padx=(self.px(6), 0), pady=(self.px(8), 0))
        return row

    def buttons(self, parent, *specs, pady=(14, 0)):
        row = tk.Frame(parent, bg=parent['bg'])
        row.pack(anchor='w', pady=(self.px(pady[0]), self.px(pady[1])))
        made = []
        for text, command, primary in specs:
            button = theme.PillButton(row, text, command, self.c, self.scale, kind='primary' if primary else 'secondary')
            button.pack(side='left', padx=(0, self.px(8)))
            made.append(button)
        return made

    def progress(self, parent, value, maximum, pady=(10, 10)):
        bar = theme.Progress(parent, self.c, self.scale)
        bar.pack(fill='x', pady=(self.px(pady[0]), self.px(pady[1])))
        bar.set(value, maximum)   # drawn once it has a size (<Configure>)
        return bar

    def status(self, text, tone='muted'):
        if self.status_label is not None and self.status_label.winfo_exists():
            self.status_label.configure(text=text, fg=self.c[tone])

    def preview_box(self, parent):
        holder = tk.Label(parent, bg=parent['bg'])
        holder.pack(anchor='w', pady=(self.px(12), 0))
        return holder

    def save_settings(self, **changes):
        self.settings = replace(self.settings, **changes)
        try:
            self.settings.save(self.settings_path)
        except (OSError, ValueError):
            pass

    # ---- background work ---------------------------------------------------
    def work(self, operation, done, failed=None, engine=False):
        def run():
            try:
                result = operation()
            except Exception as error:  # noqa: BLE001 - shown to the user in plain words
                self.events.put(('call', failed or self.show_error, error))
            else:
                self.events.put(('call', done, result))
        thread = threading.Thread(target=run, daemon=True)
        if engine:
            self.engine_thread = thread
        thread.start()

    def poll(self):
        if self.closed:
            return
        try:
            while True:
                kind, *payload = self.events.get_nowait()
                if kind == 'call':
                    function, value = payload
                    function(value)
                elif kind == 'engine':
                    self.on_engine_event(payload[0])
        except queue.Empty:
            pass
        self.after(80, self.poll)

    def show_error(self, error):
        message = str(error) if isinstance(error, (ObsError, ControllerError, NeedsAttention, ValueError)) \
            else 'Something went wrong. Check your Switch and OBS, then try again.'
        self.status(message, 'warn')

    def say_later(self, text, tone='muted'):
        """Thread-safe status update."""
        self.events.put(('call', lambda value: self.status(value, tone), text))

    # ---- live picture ------------------------------------------------------
    def start_preview(self, holder, on_frame=None, size=(COLUMN - 44, (COLUMN - 44) * 9 // 16)):
        size = (self.px(size[0]), self.px(size[1]))
        self.preview_running = True
        state = {'busy': False}

        def fetch():
            port, password = self.obs_credentials
            with ObsRecorder(port, password, timeout=5) as obs:
                return obs.frame(obs.scene())

        def show(image):
            state['busy'] = False
            if not self.preview_running or not holder.winfo_exists():
                return
            self.preview_image = ImageTk.PhotoImage(image.resize(size))
            holder.configure(image=self.preview_image)
            if on_frame:
                on_frame(image)

        def failed(_error):
            state['busy'] = False

        def tick():
            if not self.preview_running or not holder.winfo_exists():
                return
            if not state['busy'] and self.obs_credentials:
                state['busy'] = True
                self.work(fetch, show, failed)
            self.after(700, tick)
        tick()

    # ---- 1: welcome --------------------------------------------------------
    def existing_journal(self):
        try:
            return Journal.load(self.settings.output_directory)
        except Exception:
            return None

    def show_welcome(self):
        self.clear(0)
        journal = self.existing_journal()
        if journal and journal.next_pending() is not None:
            return self.show_continue(journal)
        box = self.card(padding=26)
        self.label(box, 'Record your replays', size=22, weight='bold')
        self.label(box, 'Every Smash replay on your Switch, saved as its own video.', tone='muted', pady=(2, 18))
        self.label(box, 'Save to', size=10, tone='muted', pady=(0, 4))
        row = tk.Frame(box, bg=box['bg'])
        row.pack(fill='x')
        self.folder = tk.StringVar(value=self.settings.output_directory)
        tk.Entry(row, textvariable=self.folder, font=(theme.FONT, 11), bg=self.c['well'], fg=self.c['ink'],
                 insertbackground=self.c['ink'], relief='flat', bd=0, highlightthickness=0).pack(
            side='left', fill='x', expand=True, ipady=self.px(8), ipadx=self.px(8))
        theme.PillButton(row, 'Browse', self.pick_folder, self.c, self.scale).pack(side='left', padx=(self.px(8), 0))
        if journal:
            self.label(box, f'All {journal.count} replays in this folder are recorded. New ones will be added.',
                       size=10, tone='good', pady=(10, 0))
        self.status_label = self.label(box, '', size=10, pady=(8, 0))
        self.buttons(box, ('Get started', self.leave_welcome, True), pady=(10, 0))

        needs = self.card()
        self.label(needs, 'Before you start', size=10, weight='bold', tone='muted', pady=(0, 8))
        for line in ('Switch docked, HDMI into your capture card', 'OBS open and showing your Switch',
                     'Bluetooth on this PC'):
            self.label(needs, line, pady=(0, 4))

    def show_continue(self, journal):
        """Returning user: one button picks up exactly where the last session stopped."""
        counts = journal.summary()
        saved = counts.get('done', 0) + counts.get('incomplete', 0)
        playable = journal.count - counts.get('missing', 0)
        self.folder = tk.StringVar(value=self.settings.output_directory)
        box = self.card(padding=26)
        self.label(box, 'Welcome back', size=10, weight='bold', tone='muted', pady=(0, 6))
        self.stats(box, (f'{saved}', f'of {playable} recorded'), (duration_text(journal.remaining_seconds()), 'left'))
        self.progress(box, saved, playable, pady=(14, 12))
        self.label(box, self.settings.output_directory, size=10, tone='muted')
        self.status_label = self.label(box, '', size=10, pady=(6, 0))
        self.buttons(box, ('Continue', self.continue_run, True), ('Change folder', self.pick_folder, False), pady=(12, 0))
        self.label(self.column, 'Open Replay Data on your Switch — any replay is fine.', size=10, tone='muted')

    def pick_folder(self):
        chosen = filedialog.askdirectory(parent=self, title='Where should the videos go?')
        if chosen:
            self.save_settings(output_directory=str(Path(chosen)))
            self.engine = None
            self.show_welcome()

    def continue_run(self):
        self.returning = True
        self.leave_welcome()

    def leave_welcome(self):
        folder = Path(self.folder.get().strip())
        try:
            if not folder.is_absolute():
                raise ValueError()
            folder.mkdir(parents=True, exist_ok=True)
            probe = folder / '.write-test'
            probe.write_text('ok')
            probe.unlink()
        except (OSError, ValueError):
            return self.status('That folder cannot be used. Choose another one.', 'warn')
        if str(folder) != self.settings.output_directory:
            self.engine = None
        self.save_settings(output_directory=str(folder))
        self.show_obs()

    # ---- 2: OBS ------------------------------------------------------------
    def show_obs(self):
        self.clear(1)
        box = self.card()
        self.heading(box, 'OBS')
        self.status_label = self.label(box, 'Connecting…', tone='muted')
        self.preview = self.preview_box(box)
        self.obs_buttons = self.buttons(box, ('Looks right', self.show_controller, True), ('Try again', self.show_obs, False))
        self.obs_buttons[0].configure(state='disabled')

        def connect():
            found = local_settings()
            if not found:
                raise ObsError('OBS isn\'t installed. Install OBS Studio, open it, and show your Switch in it.')
            enabled, port, password = found
            if not enabled:
                raise ObsError('In OBS, open Tools → WebSocket Server Settings, tick "Enable WebSocket server", '
                               'click OK, then press Try again.')
            with ObsRecorder(port, password) as obs:
                info = obs.inspect()
                image = obs.frame(info['scene'])
            return port, password, info, image

        def connected(result):
            port, password, info, image = result
            self.obs_credentials = (port, password)
            if info['recording'] or info['streaming']:
                return self.status('OBS is already recording or streaming. Stop it there, then press Try again.', 'warn')
            self.start_preview(self.preview)
            if screens.gray(image).mean() < 8:
                return self.status('The picture is black. Turn on your Switch and check OBS shows it.', 'warn')
            self.status('Is this your Switch?', 'ink')
            self.obs_buttons[0].configure(state='normal')
            if self.returning:
                self.status('Connected.', 'ink')
                self.after(700, self.show_controller)

        self.work(connect, connected)

    # ---- 3: controller -----------------------------------------------------
    def show_controller(self):
        self.clear(2)
        box = self.card()
        self.heading(box, 'Controller')
        self.status_label = self.label(box, 'Preparing Bluetooth…', tone='muted')
        self.preview = self.preview_box(box)
        self.buttons(box, ('Try again', self.show_controller, False))
        self.label(self.column, 'This PC\'s Bluetooth is lent to the Switch until you close the recorder.',
                   size=10, tone='muted')
        distribution = self.settings.controller_environment or DEFAULT_DISTRO

        def prepare():
            needs = setup_env.check(distribution)
            reboot = False
            if not needs['usbipd']:
                self.say_later('Installing the USB sharing helper… click Yes if Windows asks.')
                reboot |= self.bluetooth.install('install_usbipd').get('reboot', False)
            if not needs['wsl']:
                self.say_later('Installing Windows Subsystem for Linux (a few minutes)… click Yes if Windows asks.')
                reboot |= self.bluetooth.install('install_wsl').get('reboot', False)
            if reboot:
                return 'reboot'
            if not setup_env.check(distribution)['helper']:
                if distribution != setup_env.HELPER_DISTRO:
                    raise ControllerError(f'The controller environment "{distribution}" is missing.')
                self.say_later('Setting up the controller helper (about a minute)…')
                setup_env.import_helper(distribution)
            self.say_later('Connecting Bluetooth…' if self.bluetooth.uses_service
                           else 'Connecting Bluetooth… click Yes when Windows asks.')
            radio = choose_radio(self.settings.bluetooth_instance)
            self.bluetooth.bind(radio.bus_id)   # released automatically when the recorder closes
            return attach_radio(choose_radio(radio.instance_id), distribution)

        def prepared(radio):
            if radio == 'reboot':
                return self.status('Restart your PC to finish setting up, then open the recorder again.', 'warn')
            self.radio = radio
            self.save_settings(bluetooth_instance=radio.instance_id, controller_environment=distribution)
            if self.controller and self.controller.connected:
                return self.controller_ready()
            if self.settings.switch_address:
                self.status('Reconnecting to your Switch… make sure it\'s awake.')
                self.connect_controller(pair=False)
            else:
                self.ask_to_pair()

        self.work(prepare, prepared)

    def ask_to_pair(self):
        self.status('On your Switch, open Controllers → Change Grip/Order.', 'ink')
        self.pairing_started = False

        def watch(image):
            if screens.matches(image, 'pairing') and not self.pairing_started:
                self.pairing_started = True
                self.status('Pairing…')
                self.connect_controller(pair=True)
        self.start_preview(self.preview, on_frame=watch)

    def connect_controller(self, pair):
        distribution = self.settings.controller_environment or DEFAULT_DISTRO
        if self.controller:
            self.controller.stop()
        self.controller = Controller(distribution, self.settings.switch_address)

        def connect():
            self.controller.start(pair=pair)
            ok = self.controller.wait_connected(90 if pair else 25)
            if ok and pair:
                self.controller.press('a')   # "Press A when you're ready" leaves the pairing screen
            return ok

        def done(ok):
            if ok:
                self.save_settings(switch_address=self.controller.switch_address)
                self.controller_ready()
            elif pair:
                self.status('The Switch didn\'t accept the controller. Stay on Change Grip/Order and press Try again.',
                            'warn')
            else:
                self.controller.stop()
                self.ask_to_pair()
        self.work(connect, done)

    def controller_ready(self):
        self.preview_running = False
        self.status('Connected.', 'ink')
        self.after(700, self.show_replays)

    # ---- 4: replays --------------------------------------------------------
    def make_engine(self):
        if self.engine:
            self.io.controller = self.controller   # a reconnect makes a new controller
            return self.engine
        port, password = self.obs_credentials
        self.io = LiveIO(port, password, self.controller, self.settings.output_directory)
        self.engine = Engine(self.io, self.settings.output_directory,
                             emit=lambda event: self.events.put(('engine', event)))
        return self.engine

    def show_replays(self):
        self.clear(3)
        box = self.card()
        self.heading(box, 'Replays')
        self.guide_label = self.label(box, 'Checking your Switch…', size=12)
        self.preview = self.preview_box(box)
        self.status_label = self.label(box, '', size=10, tone='muted', pady=(8, 0))
        self.start_preview(self.preview)

        def prepare():
            engine = self.make_engine()
            resumed = engine.journal is not None
            return engine.prepare_session(), resumed

        def ready(result):
            count, resumed = result
            self.preview_running = False
            self.show_ready(count, resumed)
        self.work(prepare, ready, self.engine_stopped, engine=True)

    def show_ready(self, count, resumed):
        self.clear(3)
        journal = self.engine.journal
        counts = journal.summary()
        pending = journal.pending()
        if not pending:
            return self.engine_finished(None)
        saved = counts.get('done', 0) + counts.get('incomplete', 0)
        box = self.card(padding=26)
        self.stats(box, (f'{count}', 'replays'), (duration_text(journal.footage_seconds()), 'of matches'))
        meta = [f'{len(pending)} to record', f'about {duration_text(journal.remaining_seconds())}']
        if saved:
            meta.insert(0, f'{saved} already recorded')
        self.label(box, '  ·  '.join(meta), tone='muted', pady=(6, 18))
        self.label(box, 'Session length', size=10, weight='bold', tone='muted', pady=(0, 6))
        self.session_choice = tk.StringVar(value=SESSION_CHOICES[0][0])
        theme.Segmented(box, [label for label, _ in SESSION_CHOICES], self.session_choice, self.update_session_hint,
                        self.c, self.scale).pack(anchor='w')
        self.session_hint = self.label(box, '', size=10, tone='muted', pady=(8, 0))
        self.update_session_hint()
        self.buttons(box, ('Start recording', self.start_session, True), pady=(18, 0))
        self.label(self.column, 'Leave the Switch and this PC alone while it records.', size=10, tone='muted')

    def chosen_session_seconds(self):
        return dict(SESSION_CHOICES).get(self.session_choice.get())

    def update_session_hint(self):
        journal = self.engine.journal
        remaining = journal.remaining_seconds()
        limit = self.chosen_session_seconds()
        if limit is None or limit >= remaining:
            text = f'Finishes around {clock_text(remaining)}.'
        else:
            per = remaining / max(1, len(journal.pending()))
            text = f'About {max(1, int(limit / per))} replays, done around {clock_text(limit)}.'
        self.session_hint.configure(text=text)

    def start_session(self):
        self.session_seconds = self.chosen_session_seconds()
        self.show_recording()

    # ---- 5: recording ------------------------------------------------------
    def show_recording(self):
        self.clear(4)
        self.session_started = time.monotonic()
        self.session_count = 0
        journal = self.engine.journal
        box = self.card(padding=26)
        row = tk.Frame(box, bg=box['bg'])
        row.pack(anchor='w')
        self.counter = tk.Label(row, font=(theme.FONT, 30, 'bold'), bg=box['bg'], fg=self.c['ink'])
        self.counter.pack(side='left')
        self.counter_unit = tk.Label(row, font=(theme.FONT, 12), bg=box['bg'], fg=self.c['muted'])
        self.counter_unit.pack(side='left', padx=(self.px(8), 0), pady=(self.px(10), 0))
        self.bar = self.progress(box, 0, max(1, journal.count), pady=(10, 8))
        self.meta_label = self.label(box, '', tone='muted')
        self.preview = self.preview_box(box)
        self.status_label = self.label(box, '', size=10, tone='muted', pady=(10, 0))
        self.run_buttons = self.buttons(box, ('Stop after this replay', self.pause_after, False),
                                        ('Stop now', self.stop_now, False))
        self.phase_text = 'Starting'
        self.update_counter()
        keep_awake(True)
        self.work(lambda: self.engine.run(stop_after_seconds=self.session_seconds),
                  self.engine_finished, self.engine_stopped, engine=True)

    def update_counter(self):
        journal = self.engine.journal
        counts = journal.summary()
        handled = counts.get('done', 0) + counts.get('incomplete', 0) + counts.get('failed', 0)
        playable = journal.count - counts.get('missing', 0)
        self.counter.configure(text=f'{handled}')
        self.counter_unit.configure(text=f'of {playable} recorded')
        self.bar.set(handled, playable)
        parts = [self.phase_text]
        if journal.pending():
            parts.append(f'{duration_text(journal.remaining_seconds())} left')
        elapsed = time.monotonic() - self.session_started
        if self.session_seconds:
            left = self.session_seconds - elapsed
            parts.append(f'session ends around {clock_text(left)}' if left > 0 else 'stopping after this replay')
        self.meta_label.configure(text='  ·  '.join(parts))

    def on_engine_event(self, event):
        kind = event.get('type')
        guide = getattr(self, 'guide_label', None)
        recording = getattr(self, 'counter', None) is not None and self.counter.winfo_exists()
        if kind == 'guide' and guide is not None and guide.winfo_exists():
            guide.configure(text=event['text'])
        elif kind == 'counting' and guide is not None and guide.winfo_exists():
            guide.configure(text=f"Counting replays… {event['count']}")
        elif kind == 'status':
            self.status(event['text'])
        elif kind == 'progress' and recording:
            names = {'waiting': 'Starting replay', 'recording': 'Recording replay', 'saved': 'Saved replay'}
            if event.get('current') and event['phase'] in names:
                self.phase_text = f"{names[event['phase']]} {event['current']}"
            self.update_counter()
        elif kind == 'saved':
            self.session_count += 1
            if recording:
                self.update_counter()
        elif kind == 'thumbnail' and recording and self.preview.winfo_exists():
            width = self.px(COLUMN - 52)
            self.preview_image = ImageTk.PhotoImage(event['image'].resize((width, width * 9 // 16)))
            self.preview.configure(image=self.preview_image)

    def pause_after(self):
        self.engine.pause_after_current.set()
        self.status('Stopping after this replay.', 'ink')
        self.run_buttons[0].configure(state='disabled')

    def stop_now(self):
        self.engine.stop_event.set()
        self.status('Stopping… this replay will be recorded again next time.', 'ink')
        for button in self.run_buttons:
            button.configure(state='disabled')

    def summary_card(self, box, journal):
        counts = journal.summary()
        saved = counts.get('done', 0) + counts.get('incomplete', 0)
        playable = journal.count - counts.get('missing', 0)
        pairs = [(f'{saved}', f'of {playable} recorded')]
        if journal.pending():
            pairs.append((duration_text(journal.remaining_seconds()), 'left'))
        self.stats(box, *pairs, pady=(self.px(12), 0))
        self.progress(box, saved, playable, pady=(12, 0))

    def engine_finished(self, _):
        keep_awake(False)
        self.clear(4)
        journal = self.engine.journal
        counts = journal.summary()
        box = self.card(padding=26)
        self.label(box, 'All done', size=22, weight='bold')
        self.stats(box, (f"{counts.get('done', 0)}", 'videos saved'), pady=(self.px(8), 0))
        notes = []
        if counts.get('incomplete'):
            notes.append(f"{counts['incomplete']} older replays stopped partway on the Switch (saved as \"incomplete\")")
        if counts.get('failed'):
            notes.append(f"{counts['failed']} couldn't be played")
        for note in notes:
            self.label(box, note, size=10, tone='muted', pady=(6, 0))
        self.status_label = self.label(box, 'Giving Bluetooth back to Windows…', size=10, tone='muted', pady=(10, 0))
        self.work(self.release_bluetooth, lambda _: self.status('Bluetooth is back on Windows.'))
        self.buttons(box, ('Open folder', self.open_folder, True))

    def engine_stopped(self, error):
        keep_awake(False)
        journal = self.engine.journal if self.engine else None
        if self.engine:
            self.engine.stop_event.clear()
            self.engine.pause_after_current.clear()
        self.clear(4 if journal else 3)
        box = self.card(padding=26)
        if isinstance(error, StopRequested):
            self.label(box, 'Stopped for now', size=22, weight='bold')
            self.label(box, 'Open the recorder any time and press Continue to pick up here.', tone='muted', pady=(4, 0))
        else:
            self.label(box, 'Needs your attention', size=22, weight='bold', tone='warn')
            message = str(error) if isinstance(error, (NeedsAttention, ControllerError, ObsError)) \
                else 'Something unexpected happened. Your videos are safe — press Continue to try again.'
            self.label(box, message, pady=(4, 0))
        if journal:
            self.summary_card(box, journal)
        self.buttons(box, ('Continue', self.resume, True), ('Open folder', self.open_folder, False), pady=(18, 0))

    def resume(self):
        if not self.controller or not self.controller.connected:
            return self.show_controller()
        self.show_replays()

    def open_folder(self):
        os.startfile(self.settings.output_directory)

    # ---- shutdown ----------------------------------------------------------
    def release_bluetooth(self):
        """Stop the controller and give the adapter back to Windows."""
        if self.controller:
            self.controller.stop()
            self.controller = None
        if self.io:
            self.io.close()
        self.bluetooth.close()   # adapter goes back to Windows (the service also does this if we crash)
        self.bluetooth = BluetoothAccess()

    def close(self):
        running = self.engine_thread is not None and self.engine_thread.is_alive()
        if running and not messagebox.askyesno(
                'Stop recording?', 'A replay is being recorded. Stop and close? You can continue later.', parent=self):
            return
        self.preview_running = False
        if self.engine:
            self.engine.stop_event.set()
        if running:
            self.title('Smash Replay Recorder — stopping…')
            self.engine_thread.join(timeout=20)   # lets OBS stop and the file be kept
        self.closed = True
        keep_awake(False)
        self.release_bluetooth()
        self.destroy()


def run(state_directory):
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # crisp text on scaled displays
    except Exception:
        pass
    App(state_directory).mainloop()
