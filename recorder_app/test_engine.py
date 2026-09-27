"""Engine tests against a simulated Switch.

Frames are synthesized from the recognition templates themselves (template
regions pasted onto a plain or noisy canvas), so no real screenshots are needed.
"""
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image, ImageDraw

from recorder_app import engine as engine_module
from recorder_app import screens
from recorder_app.engine import DifferentSwitch, Engine, Journal, NeedsAttention, OtherCollection, StopRequested


def synth(name=None, noise=False, seed=0):
    rng = np.random.default_rng(seed)
    canvas = rng.integers(40, 200, (540, 960)).astype(np.float32) if noise else np.full((540, 960), 90, np.float32)
    if name:
        for (x1, y1, x2, y2), patch in screens.templates()[name]:
            canvas[y1:y2, x1:x2] = patch
    return Image.fromarray(canvas.clip(0, 255).astype(np.uint8)).convert('RGB')


def load_frames():
    frames = {name: synth(name) for name in ('details', 'game_end', 'end_no', 'end_yes')}
    frames['results'] = synth('results', noise=True, seed=3)
    frames['paused'] = synth('paused', noise=True, seed=2)
    frames['overlay'] = synth('overlay', noise=True)
    frames['gameplay'] = synth(noise=True, seed=1)
    error = synth('old_replay_error')
    ImageDraw.Draw(error).rectangle((190, 392, 456, 431), fill=(230, 120, 20))   # cursor on "No"
    frames['old_error_no'] = error
    return frames


class FakeSwitch:
    """Replay details / playback state machine driven by button presses."""

    def __init__(self, count, frames, match_ticks=12, old_error_on=()):
        self.labels, self.frames = list(range(count)), frames
        self.index = 0
        self.mode = 'details'
        self.ticks = 0
        self.match_ticks = match_ticks
        self.old_error_on = set(old_error_on)
        self.hidden = False
        self.clock = 0.0
        self.pressed = []
        self.recording = False
        self.recordings = 0

    @property
    def count(self):
        return len(self.labels)

    def label(self):
        return self.labels[self.index]

    def details_frame(self):
        image = self.frames['details'].copy()
        draw = ImageDraw.Draw(image)
        # Unique "timestamp" per replay inside the identity box.
        draw.rectangle((160, 336, 275, 356), fill=(40, 40, 40))
        label = self.label()
        draw.text((162, 339), f'01/{label % 28 + 1:02d} {label:03d}', fill=(250, 250, 250))
        return image

    # io interface ----------------------------------------------------------
    def frame(self):
        self.clock += .15
        if self.mode == 'details':
            return self.details_frame()
        if self.mode == 'playing':
            self.ticks += 1
            if self.label() in self.old_error_on and self.ticks > 6:
                self.mode = 'old_error'
                return self.frames['old_error_no']
            if not self.hidden:
                return self.frames['overlay']
            if self.ticks > self.match_ticks:
                self.mode = 'results'
                self.ticks = 0
                return self.frames['game_end']
            noisy = np.asarray(self.frames['gameplay']).astype(np.int16)
            noisy = np.clip(noisy + (self.ticks % 7) * 3, 0, 255).astype(np.uint8)
            return Image.fromarray(noisy)
        if self.mode == 'results':
            self.ticks += 1
            if self.ticks > 40:   # countdown ran out: autoplay the next replay
                self.index = (self.index + 1) % self.count
                self.mode, self.ticks, self.hidden = 'playing', 0, False
            return self.frames['game_end'] if self.ticks < 4 else self.frames['results']
        if self.mode == 'paused':
            return self.frames['paused']
        if self.mode == 'end_no':
            return self.frames['end_no']
        if self.mode == 'end_yes':
            return self.frames['end_yes']
        if self.mode == 'old_error':
            return self.frames['old_error_no']
        raise AssertionError(self.mode)

    def press(self, *buttons, down=.15, wait=True):
        combo = tuple(buttons)
        self.pressed.append(combo)
        assert 'minus' not in combo and 'y' not in combo, 'never touch delete-adjacent controls'
        if self.mode == 'details':
            if combo == ('r',):
                self.index = (self.index + 1) % self.count
            elif combo == ('l',):
                self.index = (self.index - 1) % self.count
            elif combo == ('a',):
                self.mode, self.ticks, self.hidden = 'playing', 0, False
            else:
                raise AssertionError(f'unexpected {combo} on details')
        elif self.mode == 'playing':
            if combo == ('x', 'down'):
                self.hidden = True
            elif combo == ('plus',):
                self.mode = 'paused'
        elif self.mode == 'paused' and combo == ('l', 'r', 'a', 'plus'):
            self.mode = 'end_no'
        elif self.mode == 'end_no' and combo == ('right',):
            self.mode = 'end_yes'
        elif self.mode == 'results' and combo == ('a',):
            self.index = (self.index + 1) % self.count   # skip the countdown
            self.mode, self.ticks, self.hidden = 'playing', 0, False
        elif self.mode == 'results' and combo == ('b',):
            self.mode = 'details'   # "B Quit" returns to the replay just played
        elif self.mode == 'end_yes' and combo == ('a',):
            self.mode = 'details'
        elif self.mode == 'old_error' and combo == ('a',):
            self.mode = 'details'

    def controller_ok(self):
        return True

    def paste_duration(self, image, text):
        """Draw an "M:SS" length using the reader's own digit templates."""
        known = screens.duration_templates()
        pixels = np.asarray(image.convert('L')).astype(np.float32)
        y1, y2 = screens.DURATION_ROWS
        slots = list(screens.DURATION_SLOTS)
        for (x1, x2), digit in zip(slots, text[0] + text[2:]):
            pixels[y1:y2, x1:x2] = known['digit' + digit] * 900 + 128
        c1, c2 = screens.DURATION_COLON
        colon = known['colon'] * 900 + 128
        pixels[y1:y2, c1:c1 + 1] = colon[:, :1]
        pixels[y1:y2, c2 - 1:c2] = colon[:, -1:]
        return Image.fromarray(pixels.clip(0, 255).astype(np.uint8)).convert('RGB')

    def record_start(self):
        assert not self.recording
        self.recording = True

    def record_stop(self):
        assert self.recording
        self.recording = False
        self.recordings += 1
        return f'raw-{self.recordings}.mkv'

    def recording_active(self):
        return self.recording

    def free_bytes(self):
        return 10 ** 12

    def sleep(self, seconds):
        self.clock += seconds

    def now(self):
        return self.clock


class Adapter:
    def __init__(self, switch):
        self.s = switch
        self.frame, self.press, self.controller_ok = switch.frame, switch.press, switch.controller_ok
        self.record_start, self.record_stop, self.free_bytes = switch.record_start, switch.record_stop, switch.free_bytes
        self.sleep, self.now = switch.sleep, switch.now

    def recording(self):
        return self.s.recording


def fake_finalize(source, target, delete_source=True):
    Path(target).write_bytes(b'video')
    return {'seconds': 60.0, 'video_packets': 3600, 'has_audio': True}


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.frames = load_frames()
        self.folder = Path(tempfile.mkdtemp())
        patcher = mock.patch.object(engine_module, 'finalize', fake_finalize)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.folder)

    def engine(self, switch):
        return Engine(Adapter(switch), self.folder)

    def test_identities_differ_and_classify(self):
        switch = FakeSwitch(3, self.frames)
        a = switch.details_frame(); switch.index = 1; b = switch.details_frame()
        self.assertEqual(screens.classify(a), 'details')
        self.assertFalse(screens.same_replay(screens.identity(a), screens.identity(b)))

    def test_inventory_counts_until_wrap_and_returns_to_start(self):
        switch = FakeSwitch(7, self.frames)
        switch.index = 0
        self.assertEqual(self.engine(switch).take_inventory(), 7)
        self.assertEqual(switch.index, 0)

    def test_records_every_replay_in_order_and_ends_on_details(self):
        switch = FakeSwitch(4, self.frames)
        engine = self.engine(switch)
        engine.run()
        self.assertEqual(engine.journal.summary(), {'done': 4})
        self.assertEqual(sorted(p.name for p in self.folder.glob('*.mp4')),
                         [f'replay-{n:03d}.mp4' for n in range(1, 5)])
        self.assertEqual(switch.mode, 'details')
        self.assertFalse(switch.recording)

    def test_pause_then_resume_from_another_replay(self):
        switch = FakeSwitch(5, self.frames)
        engine = self.engine(switch)
        with self.assertRaises(StopRequested):
            engine.run(limit=2)
        self.assertEqual(switch.mode, 'details')
        switch.index = 4          # user wandered to another replay
        resumed = Engine(Adapter(switch), self.folder)
        resumed.run()
        self.assertEqual(resumed.journal.summary(), {'done': 5})

    def test_older_replay_error_saves_partial_and_moves_on(self):
        switch = FakeSwitch(3, self.frames, old_error_on={1})
        engine = self.engine(switch)
        engine.run()
        statuses = [r.status for r in engine.journal.replays]
        self.assertEqual(statuses, ['done', 'incomplete', 'done'])
        self.assertTrue((self.folder / 'replay-002-incomplete.mp4').exists())

    def test_leaving_uses_b_on_results_not_pause_menu(self):
        switch = FakeSwitch(3, self.frames)
        engine = self.engine(switch)
        with self.assertRaises(StopRequested):
            engine.run(limit=1)
        self.assertIn(('b',), switch.pressed)
        self.assertNotIn(('plus',), switch.pressed)
        self.assertEqual((switch.mode, switch.index), ('details', 0))

    def test_new_replays_between_sessions_are_merged_and_recorded_once(self):
        switch = FakeSwitch(5, self.frames)
        first = self.engine(switch)
        with self.assertRaises(StopRequested):
            first.run(limit=2)
        switch.labels[0:0] = [100, 101]   # two matches played since; newest appear first
        switch.index = 0                  # the player opens the top-left (newest) replay
        second = Engine(Adapter(switch), self.folder)
        self.assertEqual(second.prepare_session(), 7)
        second.run()
        self.assertEqual(second.journal.summary(), {'done': 7})
        self.assertEqual(len(list(self.folder.glob('replay-*.mp4'))), 7)
        self.assertEqual(second.journal.order, [6, 7, 1, 2, 3, 4, 5])

    def test_session_time_limit_pauses_after_current_replay(self):
        switch = FakeSwitch(4, self.frames)
        engine = self.engine(switch)
        with self.assertRaises(StopRequested):
            engine.run(stop_after_seconds=1)
        self.assertEqual(engine.journal.summary(), {'done': 1, 'pending': 3})
        self.assertEqual(switch.mode, 'details')

    def test_reads_replay_length_and_estimates_time(self):
        switch = FakeSwitch(1, self.frames)
        frame = switch.paste_duration(switch.details_frame(), '2:47')
        self.assertEqual(screens.read_duration(frame), 167)
        self.assertIsNone(screens.read_duration(self.frames['gameplay']))

    def test_different_switch_is_never_merged_into_another_collection(self):
        first = FakeSwitch(5, self.frames)
        with self.assertRaises(StopRequested):
            self.engine(first).run(limit=2)
        second = FakeSwitch(4, self.frames)
        second.labels = [100, 101, 102, 103]           # another console's replays
        engine = Engine(Adapter(second), self.folder)
        with self.assertRaises(DifferentSwitch) as caught:
            engine.prepare_session()
        self.assertEqual(Journal.load(self.folder).summary(), {'done': 2, 'pending': 3})
        # The counted list starts the new folder without stepping through it again.
        presses = len(second.pressed)
        other_folder = self.folder / 'Switch 2'
        new = Engine(Adapter(second), other_folder)
        self.assertEqual(new.take_inventory(entries=caught.exception.entries), 4)
        self.assertEqual(len(second.pressed), presses)
        new.run()
        self.assertEqual(new.journal.summary(), {'done': 4})

    def test_known_switch_is_sent_to_its_own_collection(self):
        switch_a, switch_b = FakeSwitch(3, self.frames), FakeSwitch(3, self.frames)
        switch_b.labels = [200, 201, 202]
        folder_a, folder_b = self.folder / 'A', self.folder / 'B'
        Engine(Adapter(switch_a), folder_a).take_inventory()
        Engine(Adapter(switch_b), folder_b).take_inventory()
        switch_a.index = 1                             # Switch A plugged in while folder B is selected
        engine = Engine(Adapter(switch_a), folder_b)
        with self.assertRaises(OtherCollection) as caught:
            engine.prepare_session(other_collections=[folder_a, folder_b])
        self.assertEqual(Path(caught.exception.folder), folder_a)

    def test_missing_replay_returns_when_it_shows_up_again(self):
        switch = FakeSwitch(5, self.frames)
        with self.assertRaises(StopRequested):
            self.engine(switch).run(limit=1)
        switch.labels, switch.index = [50, 0, 1, 2, 4], 0    # replay 4 (label 3) gone, one new
        engine = Engine(Adapter(switch), self.folder)
        engine.prepare_session()
        self.assertEqual(engine.journal.get(4).status, 'missing')
        switch.labels, switch.index = [60, 50, 0, 1, 2, 3, 4], 0   # it is back, plus another new one
        engine = Engine(Adapter(switch), self.folder)
        engine.prepare_session()
        self.assertEqual(engine.journal.get(4).status, 'pending')
        self.assertEqual(engine.journal.count, 7)

    def test_refuses_existing_recording_set(self):
        switch = FakeSwitch(2, self.frames)
        engine = self.engine(switch)
        engine.take_inventory()
        with self.assertRaises(NeedsAttention):
            Engine(Adapter(switch), self.folder).take_inventory()


if __name__ == '__main__':
    unittest.main()
