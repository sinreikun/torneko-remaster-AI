"""Pure component checks. No demo game, live-game automation or simulated clear."""
import io
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock
import zipfile
from autoplayer.core import Settings
from autoplayer.session import SessionControl
from autoplayer.diagnostics import DiagnosticStore,InputDiagnostics,export_diagnostics
from autoplayer.recording import FrameRecorder

class RunControlTests(unittest.TestCase):
    def test_pause_wakes_action_wait_and_resume_unblocks_checkpoint(self):
        control=SessionControl();self.assertTrue(control.toggle_pause())
        self.assertTrue(control.wait(.1));self.assertEqual(control.revision,1)
        result=[];worker=threading.Thread(target=lambda:result.append(control.checkpoint()));worker.start()
        time.sleep(.03);self.assertEqual(result,[])
        self.assertFalse(control.toggle_pause());worker.join(1)
        self.assertEqual(result,[True]);self.assertEqual(control.revision,2);control.close()
    def test_stop_unblocks_pause(self):
        control=SessionControl();control.toggle_pause();control.stop.set()
        self.assertFalse(control.checkpoint());control.close()
    def test_expired_duration_stops_without_another_input(self):
        control=SessionControl();control.deadline=0
        self.assertFalse(control.checkpoint());self.assertTrue(control.stop.is_set());self.assertTrue(control.reason)
    def test_zero_step_limit_is_supported(self):
        self.assertEqual(Settings(max_steps=0).validate().max_steps,0)

class DiagnosticsTests(unittest.TestCase):
    def run_probe(self,folder,changed=0,accepted=True,stop=False,separation=False):
        backend=Mock();backend.probe_input.return_value={'os_submission':'accepted' if accepted else 'rejected','no_keys_sent':True}
        backend.capture.return_value=b'observation';backend.difference.return_value=changed
        store=DiagnosticStore(folder/'diagnostics.sqlite');events=[];control=SessionControl();control.wait=lambda seconds:False
        if stop:control.stop.set()
        times=iter([0,0,1,2,600])
        runner=InputDiagnostics(backend,store,events.append,control,clock=lambda:next(times))
        try:return runner.run(separation),events,backend
        finally:store.close();control.close()
    def test_unknown_unchanging_state_completes_without_game_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);ok,events,backend=self.run_probe(folder)
            self.assertTrue(ok);backend.act.assert_not_called();backend.matches_profile.assert_not_called()
            self.assertFalse((folder/'memory.sqlite').exists())
            self.assertEqual(events[-1]['status'],'completed')
            self.assertTrue(all(e['learning'] is False for e in events))
    def test_natural_change_is_not_an_input_success_condition(self):
        with tempfile.TemporaryDirectory() as temp:
            ok,events,backend=self.run_probe(Path(temp),changed=500)
            self.assertTrue(ok);backend.act.assert_not_called()
            self.assertTrue(all(e.get('game_response')=='not_tested' for e in events[:-1]))
    def test_os_rejection_is_a_system_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(RuntimeError):self.run_probe(Path(temp),accepted=False)
    def test_cancel_is_not_success(self):
        with tempfile.TemporaryDirectory() as temp:
            ok,events,backend=self.run_probe(Path(temp),stop=True)
            self.assertFalse(ok);backend.probe_input.assert_not_called()
    def test_ten_minute_monitor_has_no_state_or_key_requirement(self):
        with tempfile.TemporaryDirectory() as temp:
            ok,events,backend=self.run_probe(Path(temp),separation=True)
            self.assertTrue(ok);backend.act.assert_not_called()
            self.assertTrue(all(e.get('input_isolation')=='not_exercised_no_keys_sent' for e in events[:-1]))

class ReportTests(unittest.TestCase):
    def test_online_backup_includes_wal_and_actual_recording(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);db=sqlite3.connect(folder/'memory.sqlite');db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE evidence(value TEXT)');db.execute('INSERT INTO evidence VALUES(?)',('committed in WAL',));db.commit()
            frames=folder/'recordings'/'run1';frames.mkdir(parents=True);(frames/'frames.jsonl').write_text('{}\n');(frames/'screen.png').write_bytes(b'evidence')
            archive=folder/'exports'/'report.zip';report=export_diagnostics(folder,archive)
            with zipfile.ZipFile(archive) as z:
                z.extract('memory.sqlite',folder/'backup')
                self.assertIn('recordings/run1/screen.png',z.namelist());self.assertIn('health.txt',z.namelist())
            with closing(sqlite3.connect(folder/'backup'/'memory.sqlite')) as snapshot:self.assertEqual(snapshot.execute('SELECT value FROM evidence').fetchone()[0],'committed in WAL')
            self.assertEqual(report['databases']['memory.sqlite']['status'],'ok');db.close()
    def test_missing_checks_are_not_success(self):
        with tempfile.TemporaryDirectory() as temp:
            report=export_diagnostics(temp,Path(temp)/'report.zip')
            self.assertEqual(report['databases']['diagnostics.sqlite']['status'],'not_run')
    def test_corrupt_database_does_not_prevent_log_submission(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);(folder/'memory.sqlite').write_bytes(b'corrupt');(folder/'application.log').write_text('capture failed')
            archive=folder/'report.zip';report=export_diagnostics(folder,archive)
            self.assertEqual(report['databases']['memory.sqlite']['status'],'failed')
            with zipfile.ZipFile(archive) as z:self.assertEqual(z.read('application.log'),b'capture failed')
    def test_recorder_caps_storage_and_keeps_frame_hashes(self):
        with tempfile.TemporaryDirectory() as temp:
            recorder=FrameRecorder(temp,limit_bytes=4)
            recorder.record(b'abcd','observation');recorder.record(b'abcd','result');recorder.record(b'efgh','result')
            stats=recorder.close()
            self.assertEqual(stats['written'],1);self.assertEqual(stats['capped'],1);self.assertEqual(stats['used'],4)
            lines=[json.loads(x) for x in (Path(temp)/'frames.jsonl').read_text().splitlines()]
            self.assertEqual(lines[0]['sha256'],lines[1]['sha256']);self.assertIsNone(lines[-1]['file'])


class OCRComponentTests(unittest.TestCase):
    def test_original_manifest_hashes_have_complete_sizes_and_unique_ids(self):
        from autoplayer.ocr_components import MODELS
        self.assertEqual({model['id'] for model in MODELS},{'ocr-paddle-v5','ocr-paddle-ja'})
        for model in MODELS:
            for spec in model['files']:
                self.assertGreater(spec['size_bytes'],0);self.assertEqual(len(spec['sha256']),64)
                self.assertTrue(all(url.startswith('https://') for url in spec['urls']))
    def test_insecure_redirect_is_rejected_before_following(self):
        from autoplayer.ocr_components import HTTPSRedirects
        with self.assertRaises(ValueError):HTTPSRedirects().redirect_request(None,None,302,'',{},'http://invalid.test/file')
    def test_model_tamper_is_rejected(self):
        import hashlib
        from autoplayer.ocr_components import valid_file
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'file.onnx';path.write_bytes(b'good')
            spec={'size_bytes':4,'sha256':hashlib.sha256(b'good').hexdigest()}
            self.assertTrue(valid_file(path,spec));path.write_bytes(b'evil');self.assertFalse(valid_file(path,spec))
