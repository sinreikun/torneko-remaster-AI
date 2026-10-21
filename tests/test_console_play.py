"""Explicit-command gating, durable journals, and observed examples; no game."""
import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from autoplayer.console_play import ConsoleCommands,ConsolePlayer
from autoplayer.core import Settings
from autoplayer.memory import MemoryStore
from autoplayer.demonstrations import DemonstrationStore
from autoplayer.session import SessionControl

class ConsolePlayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.cfg=Settings();self.control=SessionControl();self.addCleanup(self.control.close)
        self.commands=ConsoleCommands(self.cfg);self.commands.set_ready(True)
        self.memory=MemoryStore(Path(self.tmp.name)/'memory.sqlite',self.cfg);self.addCleanup(self.memory.close)
        self.store=DemonstrationStore(self.memory,self.cfg);self.journal=Path(self.tmp.name)/'run.jsonl'
        def read(png):return {'status':'ok','frame_sha256':hashlib.sha256(png).hexdigest(),'lines':[{'text':t} for t in ['道具','マップ','足元','その他']]}
        self.backend=SimpleNamespace(capture=Mock(return_value=b'current'),act=Mock(return_value={'os_submission':'accepted','events':[]}),release_all=Mock(),
            observe_turn=Mock(return_value=SimpleNamespace(image=b'after',kind='normal')))
        self.runner=ConsolePlayer(self.backend,self.store,SimpleNamespace(read=read),self.control,self.commands,lambda _:None,self.journal,Mock())
    def command(self,keys=['num1']):
        self.assertTrue(self.commands.submit(keys,80,100));return self.commands.queue.get_nowait()
    def test_single_inflight_keeps_order_and_waits_before_reinput(self):
        command=self.command(['w','num5']);self.assertEqual(command['keys'],['num5','w'])
        self.assertFalse(self.commands.submit(['s'],80,100))
        started=time.monotonic();self.runner.execute(command)
        self.assertGreaterEqual(time.monotonic()-started,.1)
        self.assertFalse(self.commands.ready)
        rows=[json.loads(l) for l in self.journal.read_text().splitlines()]
        self.assertEqual(rows[0]['event'],'human_console_accepted');self.assertEqual(rows[-1]['event'],'human_console_result')
        self.assertTrue(rows[-1]['complete']);self.assertEqual(self.memory.db.execute('SELECT status FROM human_examples').fetchone()[0],'complete')
        self.backend.release_all.assert_called_once()
    def test_journal_failure_prevents_any_input(self):
        command=self.command()
        with patch('autoplayer.console_play.os.fsync',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.runner.execute(command)
        self.backend.act.assert_not_called()
    def test_pause_during_before_ocr_cancels_without_input(self):
        original=self.runner.ocr.read
        def pause(png):self.control.toggle_pause();self.control.toggle_pause();return original(png)
        self.runner.ocr.read=pause;self.runner.execute(self.command())
        self.backend.act.assert_not_called();self.assertIn('human_console_cancelled',self.journal.read_text())
    def test_stop_during_observation_never_creates_reusable_example(self):
        def stop(*args):self.control.stop.set();return SimpleNamespace(image=b'after',kind='aborted')
        self.backend.observe_turn.side_effect=stop;self.runner.execute(self.command())
        self.assertEqual(self.memory.db.execute('SELECT status FROM human_examples').fetchone()[0],'censored')
        self.backend.release_all.assert_called_once()
    def test_submission_failure_is_logged_as_unknown_and_stops(self):
        self.backend.act.side_effect=RuntimeError('partial send')
        with self.assertRaisesRegex(RuntimeError,'partial send'):self.runner.execute(self.command())
        row=json.loads(self.journal.read_text().splitlines()[-1]);self.assertEqual(row['input_receipt']['os_submission'],'unknown');self.assertFalse(row['complete'])
        self.backend.release_all.assert_called_once()
    def test_neutral_observation_and_closed_gate(self):
        self.runner.execute(self.command([]));self.backend.act.assert_not_called()
        self.assertEqual(self.memory.db.execute('SELECT COUNT(*) FROM human_examples').fetchone()[0],0)
        self.commands.close();self.commands.set_ready(True);self.assertFalse(self.commands.submit(['w'],80,100))
        with self.assertRaises(ValueError):self.commands.submit(['enter'],80,100)

    def test_worker_readiness_waits_even_if_observation_wait_was_interrupted(self):
        ready=[];sent=[]
        self.control.wait=Mock(return_value=True)
        def emit(row):
            if row['event']=='human_console_sent':sent.append(time.monotonic())
            if row['event']=='human_console_ready' and row['ready']:
                ready.append(time.monotonic())
                if len(ready)==1:self.assertTrue(self.commands.submit(['num1'],80,100))
                else:self.control.stop.set()
        self.runner.emit=emit
        self.runner.run()
        self.assertEqual(len(ready),2);self.assertGreaterEqual(ready[1]-sent[0],.09)
        self.assertTrue(self.commands.closed)
