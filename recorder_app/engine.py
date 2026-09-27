"""Recording engine: find the replay screen, count replays, record each one.

Device access goes through a small `io` object so the
logic can be tested without a Switch:

    io.frame() -> PIL image            io.press(*buttons, wait=True)
    io.controller_ok() -> bool         io.record_start() / io.record_stop() -> raw path
    io.recording() -> bool             io.free_bytes() -> int
    io.sleep(seconds) / io.now()

Safety rules carried over: never press anything that could delete a replay
(only A, L/R, X+Down, + and the Quit combo on known screens), never retry an
older-version replay, never overwrite a video, never count a partial as done.
"""
from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import threading
import uuid

from recorder_app import screens
from recorder_app.media import MediaError, finalize

STATE_DIR = '.smash-recorder'
MIN_FREE_BYTES = 3 * 1024 ** 3
# Wall-clock time per replay beyond its listed length (loading, saving),
# measured live (~19 s) with the results-screen countdown skipped.
OVERHEAD_SECONDS = 20
DEFAULT_REPLAY_SECONDS = 150

GUIDANCE = {
    'list': 'Highlight the replay to start from (the top-left one records everything in order) and press A.',
    'pairing': 'Press B on your controller to leave the controller screen, then open Smash Bros. Ultimate.',
    'unknown': 'On your Switch, open Super Smash Bros. Ultimate → Vault → Replays → Replay Data, '
               'then press A on the first replay.',
    'black': 'Waiting for a picture from your Switch. Make sure it is on and docked.',
}


class StopRequested(Exception):
    pass


class NeedsAttention(Exception):
    """Something the user must look at; the message says what to do."""


class DifferentSwitch(Exception):
    """The Switch's replay list has nothing in common with this folder's collection.

    Carries the replays already counted so a new collection can start without
    stepping through the list again.
    """

    def __init__(self, entries):
        super().__init__('This looks like a different Switch.')
        self.entries = entries


class OtherCollection(Exception):
    """The replay on screen belongs to another folder's collection (another Switch)."""

    def __init__(self, folder):
        super().__init__(f'This Switch is recorded in {folder}.')
        self.folder = folder


def collection_identities(folder):
    """Replay identities of the collection saved in `folder` (empty if none)."""
    journal = Journal.load(folder)
    if not journal:
        return []
    inventory = Path(folder) / STATE_DIR / 'inventory'
    return [screens.load_identity(inventory / f'{n:03d}.npz') for n in range(1, journal.count + 1)
            if (inventory / f'{n:03d}.npz').exists()]


@dataclass
class Replay:
    number: int
    status: str = 'pending'   # pending | done | incomplete | failed | missing
    file: str | None = None
    seconds: float | None = None
    note: str | None = None
    listed_seconds: int | None = None   # length shown on the Switch's details screen


@dataclass
class Journal:
    count: int
    replays: list = field(default_factory=list)
    order: list = field(default_factory=list)   # replay numbers in the Switch's list order
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    schema: int = 1

    def __post_init__(self):
        if not self.order:
            self.order = [r.number for r in self.replays]

    def get(self, number):
        return self.replays[number - 1]

    def pending(self):
        """Pending replays in list order (the order autoplay visits them)."""
        return [self.get(n) for n in self.order if self.get(n).status == 'pending']

    def pending_after(self, number):
        """Next pending replay after `number`, continuing around the list like autoplay."""
        position = self.order.index(number)
        for step in range(1, len(self.order) + 1):
            candidate = self.get(self.order[(position + step) % len(self.order)])
            if candidate.status == 'pending':
                return candidate
        return None

    def adjacent(self, first, second):
        return self.order[(self.order.index(first) + 1) % len(self.order)] == second

    def _average(self):
        known = [r.listed_seconds for r in self.replays if r.listed_seconds]
        return sum(known) / len(known) if known else DEFAULT_REPLAY_SECONDS

    def estimate_seconds(self, replays):
        """Wall-clock time to record `replays`."""
        average = self._average()
        return sum((r.listed_seconds or average) + OVERHEAD_SECONDS for r in replays)

    def remaining_seconds(self):
        return self.estimate_seconds(self.pending())

    def footage_seconds(self):
        average = self._average()
        return sum(r.listed_seconds or average for r in self.replays if r.status != 'missing')

    @classmethod
    def load(cls, folder):
        path = Path(folder) / STATE_DIR / 'run.json'
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding='utf-8'))
        data['replays'] = [Replay(**r) for r in data['replays']]
        return cls(**data)

    def save(self, folder):
        directory = Path(folder) / STATE_DIR
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / f'run.{uuid.uuid4().hex}.tmp'
        temporary.write_text(json.dumps(asdict(self), indent=2), encoding='utf-8')
        os.replace(temporary, directory / 'run.json')

    def next_pending(self):
        pending = self.pending()
        return pending[0] if pending else None

    def summary(self):
        counts = {}
        for replay in self.replays:
            counts[replay.status] = counts.get(replay.status, 0) + 1
        return counts


class Engine:
    def __init__(self, io, output_dir, emit=None):
        self.io = io
        self.output = Path(output_dir)
        self.emit = emit or (lambda event: None)
        self.stop_event = threading.Event()
        self.pause_after_current = threading.Event()
        self.journal = Journal.load(self.output)
        self.inventory = []
        if self.journal:
            self.inventory = [screens.load_identity(self._inventory_file(n))
                              for n in range(1, self.journal.count + 1)]
        self._last_guidance = None

    # -- helpers ---------------------------------------------------------
    def _inventory_file(self, number):
        return self.output / STATE_DIR / 'inventory' / f'{number:03d}.npz'

    def say(self, text, kind='status', **extra):
        self.emit({'type': kind, 'text': text, **extra})

    def progress(self, phase, current=None):
        counts = self.journal.summary() if self.journal else {}
        self.emit({'type': 'progress', 'phase': phase, 'current': current,
                   'total': self.journal.count if self.journal else None,
                   'done': counts.get('done', 0), 'incomplete': counts.get('incomplete', 0),
                   'failed': counts.get('failed', 0),
                   'remaining_seconds': self.journal.remaining_seconds() if self.journal else None})

    def check(self):
        if self.stop_event.is_set():
            raise StopRequested()
        if not self.io.controller_ok():
            deadline = self.io.now() + 20
            while not self.io.controller_ok():
                if self.io.now() > deadline:
                    raise NeedsAttention('The controller lost its connection to the Switch. '
                                         'Make sure the Switch is awake, then press Resume.')
                self.io.sleep(.5)

    def frame(self):
        self.check()
        return self.io.frame()

    def wait_for(self, names, timeout, interval=.15):
        """Poll until the screen is one of `names`; returns (name, frame)."""
        deadline = self.io.now() + timeout
        while self.io.now() < deadline:
            image = self.frame()
            name = screens.classify(image)
            if name in names:
                return name, image
            self.io.sleep(interval)
        return None, None

    # -- getting to the replay details screen ------------------------------
    def pause_playback(self):
        """Press + until the pause menu is really showing (countdowns ignore +)."""
        for _ in range(6):
            name, _ = self.wait_for({'paused', 'end_no', 'end_yes', 'details'}, .5)
            if name:
                return name
            self.io.press('plus')
            name, _ = self.wait_for({'paused', 'end_no', 'end_yes', 'details'}, 2)
            if name:
                return name
            self.io.sleep(1)
        return None

    def exit_playback(self):
        """+ pauses; L+R+A+Plus asks "End playback?"; choose Yes. Ends viewing only."""
        for attempt in range(2):
            name = self.pause_playback()
            if name == 'details':
                return
            if name == 'paused':
                self.io.sleep(.4)
                self.io.press('l', 'r', 'a', 'plus', down=.4)
            name, _ = self.wait_for({'end_no', 'end_yes', 'details'}, 6)
            if name == 'details':
                return
            if name in ('end_no', 'end_yes'):
                if name == 'end_no':
                    self.io.press('right')
                    name, _ = self.wait_for({'end_yes'}, 4)
                    if name != 'end_yes':
                        break
                self.io.press('a')
                name, _ = self.wait_for({'details'}, 20)
                if name == 'details':
                    return
                break
        raise NeedsAttention('Could not leave the replay automatically. Press + on your controller, '
                             'choose to end playback, and return to the replay details screen.')

    def find_details(self):
        """Wait (guiding the user) until the replay details screen is showing."""
        confirmations = 0
        while True:
            image = self.frame()
            name = screens.classify(image)
            if name == 'details':
                confirmations += 1
                if confirmations >= 2:
                    self._last_guidance = None
                    return image
                self.io.sleep(.3)
                continue
            confirmations = 0
            if name == 'account_error':
                raise NeedsAttention('The Switch says this Nintendo Account is in use on another console. '
                                     'Resolve that on the Switch, return to Replay Data, then press Resume.')
            if name == 'old_replay_error' and screens.old_replay_no_selected(image):
                self.io.press('a')   # "No" keeps the replay on the console.
            elif name in ('overlay', 'paused', 'game_end', 'end_no', 'end_yes'):
                self.say('Leaving the replay that is playing…')
                self.exit_playback()
                continue
            text = GUIDANCE.get(name, GUIDANCE['unknown'])
            if text != self._last_guidance:
                self._last_guidance = text
                self.say(text, kind='guide', screen=name)
            self.io.sleep(.5)

    # -- counting ----------------------------------------------------------
    def walk_list(self):
        """Step through the whole list with R; returns [(identity, listed seconds)].

        Ends back on the starting replay (the list wraps around).
        """
        start = self.find_details()
        entries = [(screens.identity(start), screens.read_duration(start))]
        wrapped = False
        while len(entries) < 2000:
            self.io.press('r')
            self.io.sleep(.75)
            name, image = self.wait_for({'details'}, 5)
            if name != 'details':
                raise NeedsAttention('The replay list closed while counting. Return to Replay Data and try again.')
            current = screens.identity(image)
            if screens.same_replay(current, entries[0][0]):
                wrapped = True
                break
            if screens.same_replay(current, entries[-1][0]):
                self.io.sleep(.8)
                image = self.frame()
                current = screens.identity(image)
                if screens.same_replay(current, entries[-1][0]):
                    break   # R stopped moving: end of list without wrapping
                if screens.same_replay(current, entries[0][0]):
                    wrapped = True
                    break
            entries.append((current, screens.read_duration(image)))
            self.emit({'type': 'counting', 'count': len(entries)})
        if not wrapped:
            for _ in range(len(entries) - 1):
                self.io.press('l')
                self.io.sleep(.4)
        return entries

    def take_inventory(self, entries=None):
        """First visit: count every replay (unless already counted) and read its length."""
        if self.journal:
            raise NeedsAttention('This folder already has a recording set. Resume it or choose another folder.')
        if entries is None:
            self.say('Counting your replays…')
            entries = self.walk_list()
        journal = Journal(count=len(entries),
                          replays=[Replay(n, listed_seconds=seconds) for n, (_, seconds) in enumerate(entries, 1)])
        self.output.mkdir(parents=True, exist_ok=True)
        (self.output / STATE_DIR / 'inventory').mkdir(parents=True, exist_ok=True)
        for number, (ident, _) in enumerate(entries, 1):
            screens.save_identity(self._inventory_file(number), ident)
        journal.save(self.output)
        self.journal, self.inventory = journal, [ident for ident, _ in entries]
        self.say(f'Found {len(entries)} replays.')
        return len(entries)

    def rescan(self):
        """Later visits: merge replays saved since last time; keep everything already recorded.

        Raises DifferentSwitch (without changing anything) when the list shares
        no replays with this collection, so another console's list is never
        merged into it.
        """
        self.say('Checking for replays saved since last time…')
        entries = self.walk_list()
        known = sum(1 for ident, _ in entries if self.locate_identity(ident) is not None)
        if known < min(2, len(self.inventory), len(entries)) or known == 0:
            raise DifferentSwitch(entries)
        order, added = [], 0
        for ident, seconds in entries:
            number = self.locate_identity(ident)
            if number is None:
                number = len(self.journal.replays) + 1
                self.journal.replays.append(Replay(number, listed_seconds=seconds))
                screens.save_identity(self._inventory_file(number), ident)
                self.inventory.append(ident)
                added += 1
            else:
                replay = self.journal.get(number)
                if replay.listed_seconds is None:
                    replay.listed_seconds = seconds
                if replay.status == 'missing':   # it came back (e.g. the list was re-sorted)
                    replay.status, replay.note = 'pending', None
            order.append(number)
        present = set(order)
        for replay in self.journal.replays:
            if replay.number not in present and replay.status == 'pending':
                replay.status, replay.note = 'missing', 'No longer on the Switch.'
        self.journal.order = order + [r.number for r in self.journal.replays if r.number not in present]
        self.journal.count = len(self.journal.replays)
        self.journal.save(self.output)
        self.say(f'Added {added} new replays.' if added else 'No new replays.')
        return added

    def prepare_session(self, other_collections=()):
        """Get this folder's collection ready for the Switch that is connected.

        - First visit to an empty folder: count the replays.
        - The replay on screen belongs to another folder's collection: raise OtherCollection.
        - The list changed: merge new replays, or raise DifferentSwitch if nothing matches.
        """
        image = self.find_details()
        if not self.journal:
            return self.take_inventory()
        if self.locate(image) is None:
            current = screens.identity(image)
            for folder in other_collections:
                if Path(folder) != self.output and any(screens.same_replay(current, known)
                                                       for known in collection_identities(folder)):
                    raise OtherCollection(folder)
            self.rescan()
        return self.journal.count

    # -- navigation --------------------------------------------------------
    def locate_identity(self, ident):
        for number, known in enumerate(self.inventory, 1):
            if screens.same_replay(ident, known):
                return number
        return None

    def locate(self, image):
        return self.locate_identity(screens.identity(image))

    def go_to(self, target):
        """Move with L/R to replay `target` using the saved list order, verifying identity."""
        image = self.find_details()
        order = [n for n in self.journal.order if self.journal.get(n).status != 'missing']
        unknown_steps = 0
        for _ in range(8):
            current = self.locate(image)
            if current == target:
                return image
            if current is None or current not in order:
                # A replay saved since the list was counted; step past it.
                unknown_steps += 1
                if unknown_steps > 40:
                    break
                self.io.press('r')
                self.io.sleep(.5)
                image = self.find_details()
                continue
            count = len(order)
            forward = (order.index(target) - order.index(current)) % count
            button, steps = ('r', forward) if forward <= count - forward else ('l', count - forward)
            self.say(f'Moving to replay {target}…')
            for _ in range(steps):
                self.io.press(button)
                self.io.sleep(.35)
            self.io.sleep(.6)
            image = self.find_details()
        raise NeedsAttention(f'Could not select replay {target}. Return to Replay Data and press Resume.')

    # -- recording ---------------------------------------------------------
    def run(self, limit=None, stop_after_seconds=None):
        """Record every pending replay; pause after `limit` replays or once the session time is used."""
        recorded = 0
        session_end = None if stop_after_seconds is None else self.io.now() + stop_after_seconds
        if not self.journal:
            self.take_inventory()
        next_replay = self.journal.next_pending()
        if next_replay is None:
            self.say('Every replay in this folder has been recorded.', kind='finished')
            return
        if self.io.recording():
            raise NeedsAttention('OBS is already recording. Stop that recording in OBS, then press Start.')
        self.go_to(next_replay.number)
        self.io.press('a')
        while next_replay is not None:
            self.check()
            if self.io.free_bytes() < MIN_FREE_BYTES:
                raise NeedsAttention('Your video folder is almost full. Free up space, then press Resume.')
            outcome = self.record_one(next_replay)
            self.journal.save(self.output)
            recorded += 1
            if limit is not None and recorded >= limit:
                self.pause_after_current.set()
            if session_end is not None and self.io.now() >= session_end:
                self.pause_after_current.set()
            following = self.journal.pending_after(next_replay.number)
            if outcome == 'old_error':
                self.recover_old_replay(next_replay.number, following)
                if following is None:
                    break
                if self.pause_after_current.is_set():
                    self.pause_after_current.clear()
                    raise StopRequested()
                self.io.press('a')
            elif (following is not None and not self.pause_after_current.is_set()
                  and not self.journal.adjacent(next_replay.number, following.number)):
                # Replays in between were recorded in an earlier session; jump ahead.
                self.leave_after_current()
                self.go_to(following.number)
                self.io.press('a')
            elif following is None or self.pause_after_current.is_set():
                self.leave_after_current()
                if following is not None:
                    self.pause_after_current.clear()
                    raise StopRequested()
            else:
                self.skip_countdown()
            next_replay = following
        self.progress('finished')
        self.say('All replays are recorded.', kind='finished')

    def skip_countdown(self):
        """A on the results screen starts the next replay without the ~20 s countdown."""
        name, _ = self.wait_for({'results', 'overlay'}, 30)
        if name == 'results':
            self.io.press('a')

    def leave_after_current(self):
        """After GAME!, results count down to the next replay; B there quits to details.

        B is pressed only while the results screen is recognized, so a late
        press can never back out of the details screen into the list.
        """
        for _ in range(20):
            name, _ = self.wait_for({'results', 'details', 'overlay'}, 45)
            if name == 'details':
                return
            if name == 'results':
                self.io.press('b')
                self.io.sleep(1.5)
                continue
            if name == 'overlay':   # the next replay started anyway
                self.io.sleep(3)
            break
        self.exit_playback()

    def record_one(self, replay):
        number = replay.number
        self.progress('waiting', number)
        self.say(f'Starting replay {number} of {self.journal.count}…')
        waiting_since = self.io.now()
        opening_seen = None
        recording = False
        started = None
        last_motion = None
        last_thumbnail = 0.0
        previous = None
        while True:
            try:
                image = self.frame()
            except StopRequested:
                if recording:
                    self._keep_interrupted(replay, 'Stopped by you before the match ended.')
                raise
            now = self.io.now()
            pixels = screens.gray(image)
            if screens.matches(image, 'account_error', pixels):
                if recording:
                    self._keep_interrupted(replay, 'Interrupted by a Nintendo Account message.')
                raise NeedsAttention('The Switch says this Nintendo Account is in use on another console. '
                                     'Resolve that on the Switch, return to Replay Data, then press Resume.')
            if screens.matches(image, 'old_replay_error', pixels):
                if recording:
                    self._save(replay, incomplete=True)
                else:
                    replay.status, replay.note = 'failed', 'This replay is from an older game version and cannot play.'
                return 'old_error'
            previous, change = screens.motion(previous, image)
            if change > .15:
                last_motion = now
            if not recording:
                if screens.matches(image, 'overlay', pixels):
                    opening_seen = opening_seen or now
                    if now - opening_seen > 8:
                        raise NeedsAttention('The replay controls could not be hidden. Return to Replay Data and press Resume.')
                    self.io.press('x', 'down', wait=False)
                    self.io.sleep(.55)
                    if screens.matches(self.frame(), 'overlay'):
                        continue   # countdown ignores the hide input; retry
                    self.io.sleep(.12)
                    clean = self.frame()
                    if screens.matches(clean, 'overlay'):
                        continue
                    if screens.gray(clean).mean() < 15:
                        raise NeedsAttention('The replay ended unexpectedly while starting. Press Resume.')
                    self.io.record_start()
                    recording, started, last_motion = True, now, now
                    self.progress('recording', number)
                    self.emit({'type': 'thumbnail', 'image': clean})
                elif now - waiting_since > 65:
                    raise NeedsAttention('The next replay did not start. Return to Replay Data and press Resume.')
            else:
                if now - started > 10 and screens.matches(image, 'overlay', pixels):
                    self._keep_interrupted(replay, 'A new replay began before the match end was seen.')
                    raise NeedsAttention('A replay ended without the usual GAME! screen. Return to Replay Data and press Resume.')
                if now - last_motion > 15:
                    self._keep_interrupted(replay, 'The picture froze during recording.')
                    raise NeedsAttention('The picture from your capture card stopped changing. '
                                         'Check the capture card, then press Resume.')
                if screens.matches(image, 'game_end', pixels):
                    self.io.sleep(1.65)
                    self._save(replay)
                    return 'done'
                if now - started > 900:
                    self._keep_interrupted(replay, 'No match end within 15 minutes.')
                    raise NeedsAttention('A replay ran longer than 15 minutes without ending. Press Resume to retry it.')
                if now - last_thumbnail > 5:
                    last_thumbnail = now
                    self.emit({'type': 'thumbnail', 'image': image})
            self.io.sleep(.12)

    def _save(self, replay, incomplete=False):
        raw = self.io.record_stop()
        name = f'replay-{replay.number:03d}' + ('-incomplete' if incomplete else '') + '.mp4'
        self.say(f'Saving replay {replay.number}…')
        try:
            details = finalize(raw, self.output / name)
        except MediaError as error:
            replay.status, replay.note, replay.file = 'failed', str(error), str(raw)
            self.journal.save(self.output)
            raise NeedsAttention(f'Replay {replay.number} could not be saved: {error}') from None
        replay.file, replay.seconds = name, details['seconds']
        replay.status = 'incomplete' if incomplete else 'done'
        if incomplete:
            replay.note = 'Older game version: the replay stopped partway. The playable part was saved.'
        self.journal.save(self.output)
        self.progress('saved', replay.number)
        self.emit({'type': 'saved', 'number': replay.number, 'file': name, 'status': replay.status})

    def _keep_interrupted(self, replay, reason):
        """Stop OBS but keep the original file; the replay stays pending for a retry."""
        try:
            raw = self.io.record_stop()
        except Exception:
            raw = None
        replay.note = reason + (f' Unfinished recording kept at {raw}.' if raw else '')
        self.journal.save(self.output)

    def recover_old_replay(self, number, following):
        """Older-version error: choose No (keeps the replay), confirm, move on. Never retried."""
        name, image = self.wait_for({'old_replay_error'}, 5)
        for _ in range(2):
            if not image or not screens.old_replay_no_selected(image):
                raise NeedsAttention('An older replay stopped with an error. On the Switch choose "No" to keep it, '
                                     'return to Replay Data, then press Resume.')
            self.io.sleep(.15)
            image = self.frame()
        self.io.press('a')
        image = self.find_details()
        if self.locate(image) != number:
            raise NeedsAttention('After an older replay error the Switch showed a different replay. '
                                 'Return to Replay Data and press Resume.')
        self.journal.save(self.output)
        if following is not None:
            self.go_to(following.number)
