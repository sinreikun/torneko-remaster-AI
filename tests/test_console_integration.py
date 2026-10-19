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
from autoplayer.diagnostics import DiagnosticStore,InputDiagnostics,diagnostic_keys,export_diagnostics
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
    def test_toggle_is_source_profile_only(self):self.assertEqual(diagnostic_keys(),['num0','num2'])
    def check_run(self,folder,return_distance=0,leak=False,profile=True,stopped=False):
        backend=Mock();backend.hwnd=100;backend.matches_profile.return_value=profile;backend.foreground.return_value=200
        backend.capture.return_value=b'baseline'
        backend.difference.side_effect=lambda a,b: 0 if b==b'baseline' else (12 if b==b'changed' else return_distance)
        backend.observe_turn.side_effect=[Mock(image=b'changed',kind='normal',duration_ms=300,raw_change=.5),Mock(image=b'returned',kind='normal',duration_ms=300,raw_change=.1)]*3
        store=DiagnosticStore(folder/'diagnostics.sqlite');events=[];control=SessionControl()
        control.wait=lambda seconds:False
        if stopped:control.stop.set()
        samples=iter([0,1]*3 if leak else [0]*6)
        runner=InputDiagnostics(backend,store,events.append,control,lambda:next(samples))
        try:return runner.run(),events,backend
        finally:store.close();control.close()
    def test_three_round_trips_write_only_diagnostic_database(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);passed,events,backend=self.check_run(folder)
            self.assertTrue(passed);self.assertEqual(backend.act.call_count,6)
            self.assertFalse((folder/'memory.sqlite').exists())
            self.assertTrue(all(row.get('learning') is False for row in events))
            with closing(sqlite3.connect(folder/'diagnostics.sqlite')) as db:
                self.assertEqual(db.execute('SELECT status FROM runs').fetchone()[0],'passed')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM cycles').fetchone()[0],3)
    def test_failure_to_return_is_not_passed(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            with self.assertRaises(RuntimeError):self.check_run(folder,return_distance=20)
            with closing(sqlite3.connect(folder/'diagnostics.sqlite')) as db:self.assertEqual(db.execute('SELECT status FROM runs').fetchone()[0],'failed')
    def test_front_app_input_leak_is_not_passed(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(RuntimeError):self.check_run(Path(temp),leak=True)
    def test_wrong_process_profile_never_receives_known_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(RuntimeError):self.check_run(Path(temp),profile=False)
            with closing(sqlite3.connect(Path(temp)/'diagnostics.sqlite')) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM cycles').fetchone()[0],0)
    def test_cancel_is_not_passed(self):
        with tempfile.TemporaryDirectory() as temp:
            passed,events,backend=self.check_run(Path(temp),stopped=True)
            self.assertFalse(passed);backend.act.assert_not_called();self.assertEqual(events[-1]['status'],'interrupted')

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
