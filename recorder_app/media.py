"""Turn an OBS recording into the user's MP4, proving no video frames were lost."""
import re
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


class MediaError(Exception):
    pass


def tool(name):
    """Bundled ffmpeg first, then PATH, then the common manual install."""
    exe = name + '.exe'
    bundled = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1])) / 'ffmpeg' / exe
    for candidate in (bundled, shutil.which(exe), Path(r'C:\ffmpeg\bin') / exe):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise MediaError(f'{name} is missing from this installation. Reinstall Smash Replay Recorder.')


def _probe(path):
    """Video packet count, duration and audio presence using ffmpeg alone.

    Stream-copying the video into the framecrc format reads every packet
    without decoding and writes one line per packet.
    """
    output = subprocess.run([tool('ffmpeg'), '-hide_banner', '-nostdin', '-i', str(path), '-map', '0:v:0',
                             '-c', 'copy', '-f', 'framecrc', '-'],
                            capture_output=True, text=True, timeout=600, creationflags=NO_WINDOW)
    if output.returncode:
        raise MediaError('The recording could not be read.')
    packets = sum(1 for line in output.stdout.splitlines() if line.startswith('0,'))
    log = output.stderr
    duration = re.search(r'Duration:\s*(\d+):(\d+):([\d.]+)', log)
    seconds = (int(duration.group(1)) * 3600 + int(duration.group(2)) * 60 + float(duration.group(3))) if duration else 0.0
    return {'video_packets': packets, 'has_audio': bool(re.search(r'Stream #0:\d+.*: Audio:', log)), 'seconds': seconds}


def wait_until_written(path, timeout=30):
    """OBS finishes writing shortly after StopRecord returns."""
    deadline = time.monotonic() + timeout
    previous, stable_since = None, None
    while time.monotonic() < deadline:
        try:
            stat = Path(path).stat()
        except FileNotFoundError:
            time.sleep(.2)
            continue
        current = (stat.st_size, stat.st_mtime_ns)
        now = time.monotonic()
        if current == previous and stat.st_size:
            stable_since = stable_since or now
            if now - stable_since >= 2:
                return
        else:
            stable_since = None
        previous = current
        time.sleep(.2)
    raise MediaError('OBS did not finish saving the recording.')


def finalize(source, target, delete_source=True):
    """Copy (no re-encode) into an MP4; verify every video packet survived.

    Never overwrites an existing file. The OBS original is removed only after
    the new file is verified, so a failure always leaves one good copy.
    """
    source, target = Path(source), Path(target)
    if target.exists():
        raise MediaError(f'{target.name} already exists; it was left untouched.')
    wait_until_written(source)
    before = _probe(source)
    if before['video_packets'] == 0:
        raise MediaError('The recording contains no video.')
    temporary = target.with_name(target.stem + '.saving.mp4')
    temporary.unlink(missing_ok=True)
    result = subprocess.run([tool('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-i', str(source),
                             '-map', '0:v:0', '-map', '0:a:0?', '-c', 'copy', '-movflags', '+faststart',
                             str(temporary)], capture_output=True, text=True, timeout=3600,
                            creationflags=NO_WINDOW)
    if result.returncode:
        temporary.unlink(missing_ok=True)
        raise MediaError('Saving the video failed; the original OBS recording was kept.')
    after = _probe(temporary)
    if after['video_packets'] != before['video_packets']:
        temporary.unlink(missing_ok=True)
        raise MediaError('Frames were lost while saving; the original OBS recording was kept.')
    os.replace(temporary, target)  # target was checked absent above
    if delete_source:
        source.unlink()
    return {'seconds': round(after['seconds'], 2), 'video_packets': after['video_packets'],
            'has_audio': after['has_audio']}
