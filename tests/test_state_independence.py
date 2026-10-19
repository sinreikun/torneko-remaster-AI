"""State-independent control and diagnostics; no demo game or synthetic UI."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from autoplayer.core import Controller,Settings,SYSTEM,JAPANESE_OUTPUT
from autoplayer.memory import MemoryStore
from autoplayer.windows import WindowsGame
from autoplayer.diagnostics import export_diagnostics
from test_agent import Backend,Script,change

class NeutralObservationTests(unittest.TestCase):
    def run_steps(self,decisions,backend=None):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        backend=backend or Backend();script=Script(decisions);events=[];stats=[]
        path=Path(temp.name)/'run.jsonl'
        agent=Controller(Settings(max_steps=len(decisions)),backend,script,events.append,path,diagnostic_sink=stats.append)
        agent.run()
        rows=[json.loads(line) for line in path.read_text().splitlines()]
        return backend,script,rows,stats
    def test_repeated_unresponsive_inputs_do_not_stop_as_backend_failure(self):
        backend,script,rows,stats=self.run_steps([change() for _ in range(12)])
        self.assertEqual(len(backend.actions),3)
        self.assertEqual(len(stats),12)
        self.assertTrue(all(row['attribution']=='undetermined_after_input' for row in stats[:3]))
        self.assertTrue(all(row['action_kind']=='observe' for row in stats[3:]))
        self.assertEqual(len([r for r in rows if r.get('action_guard')=='repeated_unconfirmed_response']),9)
    def test_low_confidence_wait_is_neutral_not_stalled_or_failure(self):
        value=change(confidence=.01,risk='uncertain',action={'keys':[],'hold_ms':1,'wait_ms':0})
        backend,script,rows,stats=self.run_steps([value]*10)
        self.assertEqual(backend.actions,[]);self.assertTrue(all(not call[2] for call in script.calls))
        self.assertTrue(all(row['action_kind']=='observe' for row in stats))
        self.assertTrue(all(row['observed_keys']==[] for row in stats))
    def test_natural_observation_and_input_response_are_separate(self):
        backend=Backend()
        backend.observe_turn=Mock(side_effect=[SimpleNamespace(image=b'latest',kind='timeout',changed=True,raw_change=.8,masked_activity=0,duration_ms=1200),SimpleNamespace(image=b'after',kind='normal',changed=True,raw_change=.9,masked_activity=0,duration_ms=1400)])
        backend,script,rows,stats=self.run_steps([change()],backend)
        self.assertEqual(script.calls[0][0],b'latest')
        self.assertEqual(stats[0]['neutral_before']['raw_change'],.8)
        self.assertEqual(stats[0]['raw_change_after'],.9)
        self.assertEqual(stats[0]['attribution'],'undetermined_after_input')
    def test_model_human_fields_are_requested_in_japanese(self):
        self.assertIn(JAPANESE_OUTPUT,SYSTEM);self.assertIn('日本語',SYSTEM)
        self.assertIn('JSONのキー名',JAPANESE_OUTPUT);self.assertIn('OCR引用は原文',JAPANESE_OUTPUT)
    def test_diagnostics_cannot_be_inserted_into_game_memory(self):
        with tempfile.TemporaryDirectory() as temp:
            store=MemoryStore(Path(temp)/'memory.sqlite',Settings())
            try:
                with self.assertRaises(ValueError):store.remember({'event':'diagnostic','learning':False})
                self.assertEqual(store.history(),[])
            finally:store.close()

class OSProbeTests(unittest.TestCase):
    def test_probe_is_wm_null_not_a_game_key(self):
        backend=WindowsGame.__new__(WindowsGame);backend.hwnd=42;backend.ready=Mock()
        backend.u=SimpleNamespace(PostMessageW=Mock(return_value=True))
        receipt=backend.probe_input();backend.u.PostMessageW.assert_called_once_with(42,0,0,0)
        self.assertEqual(receipt['key_submission'],'not_tested');self.assertEqual(receipt['game_processing'],'unverified')
    def test_rejected_os_submission_raises_without_game_response_test(self):
        backend=WindowsGame.__new__(WindowsGame);backend.hwnd=42;backend.ready=Mock()
        backend.u=SimpleNamespace(PostMessageW=Mock(return_value=False))
        with self.assertRaises(RuntimeError):backend.probe_input()

class CompactReportTests(unittest.TestCase):
    def test_compact_report_keeps_full_local_data_and_limits_attachment(self):
        import os,zipfile
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);frames=root/'recordings'/'run';frames.mkdir(parents=True)
            content=os.urandom(1024*1024)
            for i in range(34):(frames/f'{i:02}.png').write_bytes(content)
            (frames/'recording.json').write_text('{}')
            path=root/'report.zip';report=export_diagnostics(root,path)
            self.assertLess(path.stat().st_size,28*1024*1024)
            self.assertEqual(len(list(frames.glob('*.png'))),34)
            with zipfile.ZipFile(path) as z:self.assertEqual(len([n for n in z.namelist() if n.endswith('.png')]),2)
            self.assertEqual(report['bundle_mode'],'compact')

class JapaneseResponseTests(unittest.TestCase):
    def test_translation_cannot_change_keys_or_evidence(self):
        from autoplayer.core import LocalLLM,parse_decision
        llm=LocalLLM.__new__(LocalLLM)
        decision=parse_decision(json.dumps(change(observation='Visible corridor',subgoal='Explore corridor',reason='Find stairs',plan=[],progress=[],memory_updates=[],goal_success='',goal_evidence=['HP 13/15'])),Settings())
        llm._request=Mock(return_value=json.dumps({'texts':['通路が見える','通路を調べる','階段を探す']}))
        translated=llm.localize_decision(decision)
        self.assertEqual(translated.keys,decision.keys)
        self.assertEqual(translated.goal_evidence,['HP 13/15'])
        self.assertEqual(translated.observation,'通路が見える')
        self.assertEqual(decision.observation,'Visible corridor')
    def test_failed_translation_becomes_neutral_observation(self):
        from autoplayer.core import LocalLLM,parse_decision
        llm=LocalLLM.__new__(LocalLLM);llm._request=Mock(return_value='{}')
        decision=parse_decision(json.dumps(change(reason='Start selected')),Settings())
        translated=llm.localize_decision(decision)
        self.assertEqual(translated.keys,[]);self.assertEqual(translated.status,'continue')
        self.assertEqual(translated.memory_updates,[])

    def test_response_with_japanese_prose_needs_no_translation(self):
        from autoplayer.core import LocalLLM,parse_decision
        llm=LocalLLM.__new__(LocalLLM);llm._request=Mock()
        decision=parse_decision(json.dumps(change()),Settings())
        self.assertIs(llm.localize_decision(decision),decision)
        llm._request.assert_not_called()
