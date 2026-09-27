"""Recognize the few Switch/Smash screens the recorder cares about.

Frames are PIL images of the capture card at any resolution; everything is
compared at 960x540 grayscale. Templates (assets/screens.npz) hold small crops of
fixed UI only: prompts, headers and dialog text.
"""
from functools import lru_cache
from pathlib import Path
import sys

import numpy as np
from PIL import Image

SIZE = (960, 540)

# Replay-details regions that differ between replays: rules header, creation
# timestamp and duration. The animated play button is excluded.
IDENTITY_BOXES = ((100, 36, 780, 72), (109, 336, 275, 356), (583, 90, 634, 112))

THRESHOLDS = {
    'pairing': .9,
    'details': .9,
    'list': .9,
    'game_end': .75,
    'overlay': .8,
    'old_replay_error': .96,
    'account_error': .9,
    'paused': .6,   # edge-matched; measured 0.88 paused vs <=0.04 gameplay
    'end_no': .97,
    'end_yes': .97,
    'results': .6,  # edge-matched
}


def _asset_path():
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    return base / 'recorder_app/assets/screens.npz'


@lru_cache(maxsize=1)
def templates():
    with np.load(_asset_path()) as data:
        grouped = {}
        for key in data.files:
            if key.endswith('.box') or key.startswith('duration:'):
                continue
            name = key.split('.')[0]
            grouped.setdefault(name, []).append((tuple(int(v) for v in data[key + '.box']), data[key]))
        return grouped


def gray(frame):
    image = frame.convert('L')
    if image.size != SIZE:
        image = image.resize(SIZE)
    return np.asarray(image).astype(np.float32)


def _correlation(a, b):
    a, b = a.ravel(), b.ravel()
    if a.std() < 2 or b.std() < 2:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _edge_correlation(patch, reference, name):
    # Horizontal text edges: the replay overlay and pause menu are translucent,
    # so the stage shining through defeats a plain pixel correlation.
    if name == 'overlay':
        patch, reference = patch[3:21, 10:120], reference[3:21, 10:120]
    return _correlation(np.diff(patch, axis=1), np.diff(reference, axis=1))


EDGE_MATCHED = {'overlay', 'paused', 'results'}


def score(frame, name, pixels=None):
    """Lowest template correlation for the named screen (all regions must match)."""
    pixels = gray(frame) if pixels is None else pixels
    results = []
    for (x1, y1, x2, y2), reference in templates()[name]:
        patch = pixels[y1:y2, x1:x2]
        results.append(_edge_correlation(patch, reference, name) if name in EDGE_MATCHED
                       else _correlation(patch, reference))
    return min(results)


def matches(frame, name, pixels=None):
    return score(frame, name, pixels) > THRESHOLDS[name]


def classify(frame):
    """Best-known screen name, or 'unknown'. Errors are checked first."""
    pixels = gray(frame)
    for name in ('account_error', 'old_replay_error', 'end_yes', 'end_no', 'paused', 'results', 'pairing',
                 'details', 'list', 'game_end', 'overlay'):
        if score(frame, name, pixels) > THRESHOLDS[name]:
            return name
    if pixels.mean() < 12:
        return 'black'
    return 'unknown'


DURATION_ROWS = (93, 107)
DURATION_SLOTS = ((593, 603), (604, 614), (613, 623))
DURATION_COLON = (601, 607)


@lru_cache(maxsize=1)
def duration_templates():
    with np.load(_asset_path()) as data:
        return {key.split(':', 1)[1]: data[key] for key in data.files if key.startswith('duration:')}


def _normalized(patch):
    patch = patch - patch.mean()
    norm = np.linalg.norm(patch)
    return patch / norm if norm else patch


def read_duration(frame, pixels=None):
    """Replay length in seconds from the details screen, or None if unsure.

    Reads the fixed-width "M:SS" label. Validated on 772 real replays (no
    misreads); lengths of 10 minutes or more use another layout and return None.
    """
    pixels = gray(frame) if pixels is None else pixels
    known = duration_templates()
    y1, y2 = DURATION_ROWS
    colon = _normalized(pixels[y1:y2, DURATION_COLON[0]:DURATION_COLON[1]])
    if float((colon * known['colon']).sum()) < .5:
        return None
    digits = []
    for x1, x2 in DURATION_SLOTS:
        best, digit = max((float((_normalized(pixels[y1:y2, x1 + dx:x2 + dx]) * known['digit' + d]).sum()), d)
                          for d in '0123456789' for dx in (-1, 0, 1))
        if best < .5:
            return None
        digits.append(int(digit))
    if digits[1] > 5:
        return None
    return digits[0] * 60 + digits[1] * 10 + digits[2]


def old_replay_no_selected(frame):
    """The older-version error with the orange cursor on No (never Yes)."""
    if not matches(frame, 'old_replay_error'):
        return False
    rgb = np.asarray(frame.convert('RGB').resize(SIZE)).astype(float)

    def orange(box):
        x1, y1, x2, y2 = box
        r, g, b = rgb[y1:y2, x1:x2].transpose(2, 0, 1)
        return ((r > 180) & (g > 65) & (g < 180) & (b < 65)).mean()
    return orange((190, 392, 456, 431)) > .30 and orange((510, 392, 760, 431)) < .05


def identity(frame):
    """Compact replay identity from a details screen."""
    pixels = gray(frame)
    return [pixels[y1:y2, x1:x2].copy() for x1, y1, x2, y2 in IDENTITY_BOXES]


def identity_distance(a, b):
    """Worst per-region mean absolute pixel difference.

    Measured on 868 real details screens: the same replay scores ~0, while
    different replays never came closer than 2.3 (a one-digit timestamp change).
    """
    return float(max(np.abs(x - y).mean() for x, y in zip(a, b)))


def same_replay(a, b, threshold=1.0):
    return identity_distance(a, b) < threshold


def save_identity(path, ident):
    np.savez_compressed(path, *ident)


def load_identity(path):
    with np.load(path) as data:
        return [data[f'arr_{i}'] for i in range(len(data.files))]


def motion(previous, frame):
    """Mean absolute difference of small thumbnails (0 when frozen)."""
    small = np.asarray(frame.convert('RGB').resize((160, 90))).astype(np.int16)
    if previous is None:
        return small, float('inf')
    return small, float(np.abs(small - previous).mean())


if __name__ == '__main__':
    for path in sys.argv[1:]:
        with Image.open(path) as image:
            pixels = gray(image)
            print(Path(path).name, classify(image),
                  {name: round(score(image, name, pixels), 3) for name in THRESHOLDS})
