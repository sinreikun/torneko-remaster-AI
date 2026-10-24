"""State-independent control and diagnostics; no demo game or synthetic UI."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from autoplayer.core import Controller,Settings,SYSTEM,JAPANESE_OUTPUT
from autoplayer.memory import MemoryStore
from autoplayer.windows import WindowsGame
from autoplayer.diagnostics import export_diagnostics
from test_agent import Backend,Script,change,OBSERVED

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
        self.assertEqual(len(backend.actions),len(script.calls))
        self.assertEqual(len(stats),12)
        self.assertTrue(all(row['attribution']=='undetermined_after_input' for row in stats[:3]))
        self.assertTrue(all(row['action_kind']=='input' for row in stats))
        self.assertGreater(len({tuple(k) for k,ms in backend.actions}),1)
        self.assertTrue(any(r.get('active_input') for r in rows if r.get('event')=='result'))
    def test_tiny_animation_does_not_release_held_actions(self):
        backend=Backend()
        def observation(*args):return SimpleNamespace(image=b'latest',kind='normal',changed=True,raw_change=2/768,masked_activity=0,duration_ms=1200)
        backend.observe_turn=Mock(side_effect=observation)
        backend,script,rows,stats=self.run_steps([change() for _ in range(8)],backend)
        self.assertEqual(len(backend.actions),len(script.calls))
        self.assertTrue(all(not r['recovery_context_change'] for r in rows if r.get('event')=='result'))
        self.assertTrue(all(r['changed_after'] for r in stats))
    def test_held_action_has_bounded_cooldown_without_stopping(self):
        backend,script,rows,stats=self.run_steps([change() for _ in range(16)])
        self.assertEqual(len(stats),16)
        self.assertEqual(len(backend.actions),16)
    def test_low_confidence_wait_proposal_becomes_active_trial(self):
        value=change(confidence=.01,risk='uncertain',action={'keys':[],'hold_ms':1,'wait_ms':0})
        backend,script,rows,stats=self.run_steps([value]*10)
        self.assertEqual(len(backend.actions),10);self.assertGreater(len({tuple(k) for k,ms in backend.actions}),1)
        self.assertTrue(all(row['action_kind']=='input' for row in stats))
        self.assertTrue(all(row['observed_keys'] for row in stats))
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

class RuntimeReportTailTests(unittest.TestCase):
    def test_large_ollama_error_log_keeps_latest_lines_and_records_truncation(self):
        import zipfile
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);data=root/'data';data.mkdir()
            log=root/'ollama.stderr.log';log.write_bytes(b'old line\n'*300000+b'recent runtime warning\n')
            with patch.dict('os.environ',{'GAMEPILOT_DATA_DIR':str(data)}):
                report=export_diagnostics(data,root/'report.zip')
            with zipfile.ZipFile(root/'report.zip') as archive:
                tail=archive.read('runtime/ollama.stderr.log')
                self.assertTrue(tail.endswith(b'recent runtime warning\n'))
                self.assertLessEqual(len(tail),2*1024*1024)
            self.assertEqual(log.stat().st_size,2700023)
            self.assertTrue(any(item['file']=='runtime/ollama.stderr.log' for item in report['truncated']))

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

class Diagnostic4404RegressionTests(unittest.TestCase):
    def setUp(self):
        from autoplayer.core import LocalLLM
        observer=patch.object(LocalLLM,'observe_scene',return_value=OBSERVED)
        observer.start();self.addCleanup(observer.stop)
    def test_placeholder_answer_is_observation_only_and_not_learning(self):
        from autoplayer.core import LocalLLM
        llm=LocalLLM(Settings(window_title='Other game'))
        llm._request=Mock(return_value=json.dumps(change(observation='現在の画面で確認できる事実',subgoal='次の到達目標',reason='その行動を選ぶ理由')))
        decision=llm.decide(b'unused-by-mocked-request',[],False)
        self.assertEqual(decision.keys,[]);self.assertEqual(decision.status,'continue')
        self.assertEqual(llm.take_state()['learning'],False)
        self.assertNotIn('autonomous_goal',llm.take_state())
        self.assertTrue(llm.needs_regrounding)
        self.assertNotIn('現在の画面で確認できる事実',SYSTEM)
    def test_recovery_schema_excludes_held_key_and_suspends_old_claims(self):
        from autoplayer.core import LocalLLM
        llm=LocalLLM(Settings(window_title='Other game'))
        llm.set_context({'unresponsive_actions':[['s']],'autonomous_goal':{'objective':'次の到達目標'},'provisional_claims':[{'value':'old claim'}],'neutral_observation':{'raw_change':0}})
        llm.knowledge_engine.goal={'objective':'次の到達目標'}
        llm._request=Mock(return_value=json.dumps(change(action={'keys':['w'],'hold_ms':80,'wait_ms':0})))
        decision=llm.decide(b'unused-by-mocked-request',[{'observation':'現在の画面で確認できる事実','subgoal':'次の到達目標','keys':['s'],'executed':True,'changed':False}],True)
        _,_,prompt,schema=llm._request.call_args.args
        self.assertEqual(decision.keys,['w'])
        self.assertTrue(prompt['fresh_assessment'])
        self.assertNotIn('provisional_claims',prompt['memory'])
        self.assertNotIn('autonomous_goal',prompt['memory'])
        self.assertNotIn('observation',prompt['recent_history'][0])
        self.assertIsNone(llm.knowledge_engine.goal)
        self.assertNotIn('s',prompt['allowed_keys'])
        for branch in schema['oneOf']:
            if branch['properties']['status']['enum']==['complete']:
                self.assertEqual(branch['properties']['action']['properties']['keys']['maxItems'],0)
            else:
                choices=branch['properties']['action']['properties']['keys']['enum']
                self.assertNotIn(['s'],choices);self.assertNotIn([],choices);self.assertIn(['w'],choices)
    def test_low_quality_results_never_enter_game_memory(self):
        from autoplayer.core import LocalLLM
        with tempfile.TemporaryDirectory() as folder:
            settings=Settings(window_title='Other game',max_steps=1)
            memory=MemoryStore(Path(folder)/'memory.sqlite',settings)
            try:
                llm=LocalLLM(settings)
                llm._request=Mock(return_value=json.dumps(change(observation='現在の画面で確認できる事実')))
                backend=Backend()
                Controller(settings,backend,llm,lambda row:None,Path(folder)/'run.jsonl',memory=memory).run()
                self.assertEqual(len(backend.actions),1);self.assertEqual(memory.history(),[])
            finally:memory.close()

class SceneObservationSeparationTests(unittest.TestCase):
    def test_scene_observer_has_no_goal_or_past_captions(self):
        from autoplayer.core import LocalLLM
        llm=LocalLLM(Settings(window_title='Other game',goal='非公開の目標'))
        observation={**OBSERVED,'scene_type':'gameplay','description':'通路とキャラクターが見える','open_routes':['右側に通路が見える']}
        llm.ocr.read=Mock(return_value={'status':'ok','lines':[{'text':'HP 15/15'}]})
        llm._request=Mock(side_effect=[json.dumps(observation),json.dumps(change())])
        llm.set_context({'provisional_claims':[{'value':'過去のロビー説明'}]})
        llm.decide(b'captured-image',[{'observation':'過去のロビー説明','subgoal':'以前の目標'}],False)
        first,second=llm._request.call_args_list
        observer_prompt=first.args[2];planner_prompt=second.args[2]
        self.assertNotIn('非公開の目標',json.dumps(observer_prompt,ensure_ascii=False))
        self.assertNotIn('過去のロビー説明',json.dumps(observer_prompt,ensure_ascii=False))
        self.assertNotIn('goal',observer_prompt);self.assertNotIn('source_knowledge',observer_prompt)
        self.assertEqual(planner_prompt['scene_observation']['open_routes'],['右側に通路が見える'])
        self.assertNotIn('status',planner_prompt['scene_observation'])
        self.assertEqual(llm.take_state()['scene_observation']['status'],'model_observation_unverified')
        self.assertEqual(first.args[0],b'captured-image')
        self.assertIsNone(second.args[0])
    def test_failed_observation_does_not_fall_back_to_old_plan(self):
        from autoplayer.core import LocalLLM
        llm=LocalLLM(Settings(window_title='Other game'))
        llm.ocr.read=Mock(return_value={'status':'unavailable','lines':[]})
        llm._request=Mock(return_value='{}')
        decision=llm.decide(b'captured-image',[{'keys':['enter'],'observation':'以前の開始画面'}],False)
        self.assertEqual(decision.keys,[]);self.assertEqual(decision.status,'continue')
        self.assertEqual(llm._request.call_count,1)
        self.assertFalse(llm.take_state()['learning'])
        self.assertEqual(llm.take_state()['scene_observation']['status'],'unavailable')

class Diagnostic7577RegressionTests(unittest.TestCase):
    def test_instruction_copy_is_not_a_gameplay_goal(self):
        from autoplayer.core import parse_decision,response_quality_issue
        instruction='次の具体的な到達点と可視の達成条件を設定してください。'
        decision=parse_decision(json.dumps(change(subgoal=instruction)),Settings())
        self.assertIn('コピー',response_quality_issue(decision,('全体目標へつながる'+instruction,)))
        good=parse_decision(json.dumps(change(subgoal='右側に見える通路の入口まで移動する')),Settings())
        self.assertEqual(response_quality_issue(good,(instruction,)),'')
    def test_observation_metadata_is_not_a_scene_choice(self):
        from autoplayer.core import parse_decision,response_quality_issue,decision_schema
        decision=parse_decision(json.dumps(change(scene='model_observation_unverified')),Settings())
        self.assertIn('状態ラベル',response_quality_issue(decision))
        for branch in decision_schema(Settings())['oneOf']:
            choices=branch['properties']['scene']['enum']
            self.assertNotIn('model_observation_unverified',choices)
            self.assertIn('unknown',choices);self.assertIn('gameplay',choices)
