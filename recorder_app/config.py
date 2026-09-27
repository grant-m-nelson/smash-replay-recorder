"""Portable non-secret setup configuration and fail-closed recording readiness.

These structures deliberately have no default Switch address, radio, count or scene.
"""
from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import re
import uuid


@dataclass(frozen=True)
class RecorderConfig:
    output_directory: str
    obs_port: int = 4455
    obs_source: str = ''
    bluetooth_instance: str = ''
    controller_environment: str = ''
    switch_address: str = ''
    expected_replays: int | None = None
    accepted_opening_trim: bool = False
    reclaim_account: bool = False
    schema_version: int = 1

    def validate(self):
        if self.schema_version != 1: raise ValueError('This settings version is not supported.')
        if not self.output_directory.strip(): raise ValueError('Choose a folder for your videos.')
        if not Path(self.output_directory).is_absolute():
            raise ValueError('Choose a full output-folder path.')
        if type(self.obs_port) is not int or not 1 <= self.obs_port <= 65535:
            raise ValueError('OBS port must be a number from 1 to 65535.')
        if self.expected_replays is not None and (type(self.expected_replays) is not int or self.expected_replays < 1):
            raise ValueError('Replay count must be discovered or a positive whole number.')
        if self.switch_address and not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', self.switch_address):
            raise ValueError('The saved Switch connection address is invalid. Pair again.')
        if any(type(x) is not bool for x in [self.accepted_opening_trim, self.reclaim_account]):
            raise ValueError('Invalid recording preferences.')
        return self

    def save(self, path: Path):
        self.validate()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.pending')
        try:
            temporary.write_text(json.dumps(asdict(self), indent=2) + '\n', encoding='utf-8')
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @classmethod
    def load(cls, path: Path):
        values = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(values, dict): raise ValueError('Settings must be an object.')
        unknown = set(values) - set(cls.__dataclass_fields__)
        if unknown: raise ValueError('Unrecognized settings; migrate rather than discard them.')
        return cls(**values).validate()


@dataclass(frozen=True)
class Finding:
    code: str
    title: str
    next_action: str


@dataclass
class Readiness:
    """Populated by live probes, never loaded as trusted facts from saved settings."""
    obs_connected: bool = False
    obs_recording: bool = False
    obs_streaming: bool = False
    source_present: bool = False
    frames_changing: bool = False
    game_audio_verified: bool = False
    radio_detected: bool = False
    controller_backend_ready: bool = False
    paired: bool = False
    input_response_verified: bool = False
    inventory_verified: bool = False
    sample_recording_verified: bool = False
    free_bytes: int = 0
    required_bytes: int = 0
    another_run_owns_devices: bool = False
    findings: list[Finding] = field(default_factory=list)

    def blockers(self, config: RecorderConfig):
        config.validate()
        checks = [
            (not self.another_run_owns_devices, 'run_busy', 'A recorder is already using these devices.', 'Return to the existing recording session.'),
            (self.obs_connected, 'obs_connection', 'Connect to OBS.', 'Open OBS and follow the connection step.'),
            (not self.obs_recording and not self.obs_streaming, 'obs_busy', 'OBS is already recording or streaming.', 'Finish that session before starting this recorder.'),
            (self.source_present and bool(config.obs_source), 'source', 'Choose your capture card.', 'Select the source showing your Switch.'),
            (self.frames_changing, 'picture', 'A moving Switch picture has not been verified.', 'Move a menu selection on the Switch and check the preview.'),
            (self.game_audio_verified, 'audio', 'Game sound has not been verified.', 'Complete the picture and sound test.'),
            (self.radio_detected, 'radio', 'Choose a Bluetooth adapter.', 'Connect or enable a supported adapter.'),
            (self.controller_backend_ready, 'backend', 'The Bluetooth helper is not ready.', 'Complete the computer preparation step.'),
            (self.paired and self.input_response_verified, 'pairing', 'Controller input has not been verified.', 'Complete pairing and the Switch input test.'),
            (self.inventory_verified and config.expected_replays is not None, 'inventory', 'Replay collection has not been checked.', 'Complete the replay selection step.'),
            (self.sample_recording_verified, 'sample', 'Test recording has not passed.', 'Record and verify one replay first.'),
            (config.accepted_opening_trim, 'trim', 'Review the short opening trim.', 'Watch the sample and accept the recording settings.'),
            (self.required_bytes > 0 and self.free_bytes >= self.required_bytes, 'space', 'Check space for the collection.', 'Choose a folder with enough free space or record fewer replays.'),
        ]
        return self.findings + [Finding(code, title, action) for passed, code, title, action in checks if not passed]

    def can_start(self, config):
        return not self.blockers(config)
