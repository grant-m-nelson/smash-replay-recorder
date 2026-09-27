import tempfile
from pathlib import Path
import unittest
from recorder_app.config import RecorderConfig, Readiness


class PortableSetupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = RecorderConfig(self.directory.name, obs_source='My capture source', expected_replays=7,
                                     accepted_opening_trim=True)

    def ready(self):
        return Readiness(obs_connected=True, source_present=True, frames_changing=True,
                         game_audio_verified=True, radio_detected=True, controller_backend_ready=True,
                         paired=True, input_response_verified=True, inventory_verified=True,
                         sample_recording_verified=True, free_bytes=200, required_bytes=100)

    def test_radio_presence_is_not_controller_compatibility(self):
        state=self.ready();state.controller_backend_ready=False
        self.assertFalse(state.can_start(self.config))
        self.assertIn('backend',[x.code for x in state.blockers(self.config)])

    def test_existing_recording_stream_or_owner_blocks_start(self):
        for name in ['obs_recording','obs_streaming','another_run_owns_devices']:
            with self.subTest(name=name):
                state=self.ready();setattr(state,name,True)
                self.assertFalse(state.can_start(self.config))

    def test_pairing_without_visible_input_or_failed_sample_blocks_start(self):
        for name in ['input_response_verified','sample_recording_verified','game_audio_verified','frames_changing']:
            with self.subTest(name=name):
                state=self.ready();setattr(state,name,False)
                self.assertFalse(state.can_start(self.config))
        self.assertTrue(self.ready().can_start(self.config))

    def test_unknown_inventory_and_disk_estimate_do_not_pass(self):
        self.assertFalse(self.ready().can_start(RecorderConfig(self.directory.name)))
        state=self.ready();state.required_bytes=0
        self.assertFalse(state.can_start(self.config))
        state.required_bytes=201
        self.assertFalse(state.can_start(self.config))

    def test_settings_round_trip_has_no_personal_defaults_or_secrets(self):
        path=Path(self.directory.name)/'settings.json';self.config.save(path)
        loaded=RecorderConfig.load(path)
        self.assertEqual(loaded,self.config)
        self.assertEqual(loaded.switch_address,'')
        self.assertFalse(loaded.reclaim_account)
        self.assertNotIn('password',path.read_text())

    def test_unknown_or_invalid_settings_are_not_silently_accepted(self):
        for changes in [{'expected_replays':0},{'expected_replays':True},{'obs_port':-1},
                        {'switch_address':'not an address'},{'schema_version':2}]:
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                RecorderConfig(**{**self.config.__dict__,**changes}).validate()


if __name__=='__main__':unittest.main()
