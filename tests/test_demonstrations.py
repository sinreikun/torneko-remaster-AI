"""Sample edges, target filtering, SQLite retrieval, and worker contracts only."""
import copy
import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from autoplayer.core import Settings,LocalLLM
from autoplayer.memory import MemoryStore
from autoplayer.demonstrations import HumanSegments,DemonstrationStore,DemonstrationRecorder,event_steps,features
from autoplayer.session import SessionControl
from autoplayer.windows import WindowsGame
from test_agent import change


def frame(start,end):return {'png':b'capture-contract','started':start,'ended':end}
def ocr(png=b'capture-contract'):
    return {'status':'ok','frame_sha256':hashlib.sha256(png).hexdigest(),'lines':[{'text':s} for s in ['道具','マップ','足元','その他']]}


class SegmentTests(unittest.TestCase):
    def test_before_is_previous_sample_frame_and_after_follows_release(self):
        segmenter=HumanSegments();first=frame(0,.01)
        segmenter.feed(True,[],first,.02)
        segmenter.feed(True,['num5'],frame(.03,.04),.05)
        segmenter.feed(True,['num5','w'],frame(.06,.07),.08)
        segmenter.feed(True,[],frame(.09,.10),.12)
        rows=segmenter.feed(True,[],frame(.35,.38),.50)
        self.assertEqual(len(rows),1);self.assertTrue(rows[0]['complete'])
        self.assertIs(rows[0]['before'],first)
        self.assertEqual(rows[0]['steps'],[{'keys':['num5'],'duration_ms':30},{'keys':['num5','w'],'duration_ms':40}])

    def test_focus_loss_and_held_keys_on_focus_gain_do_not_make_complete_examples(self):
        segmenter=HumanSegments();f=frame(0,.01)
        segmenter.feed(True,[],f,.02);segmenter.feed(True,['w'],f,.04)
        interrupted=segmenter.feed(False,[],None,.06)
        self.assertFalse(interrupted[0]['complete'])
        segmenter.feed(True,['w'],f,.08);segmenter.feed(True,['w','a'],f,.10)
        self.assertIsNone(segmenter.current)
        segmenter.feed(True,[],f,.12);segmenter.feed(True,['a'],frame(.13,.14),.16)
        self.assertEqual(segmenter.current['events'][0]['key'],'a')

    def test_late_capture_and_contaminated_after_frame_are_censored(self):
        segmenter=HumanSegments();f=frame(0,1.0)
        segmenter.feed(True,[],f,1.01);segmenter.feed(True,['w'],f,1.03)
        segmenter.feed(True,[],f,1.05)
        rows=segmenter.feed(True,['a'],f,1.50)
        self.assertFalse(rows[0]['complete']);self.assertEqual(rows[0]['reason'],'next_input_before_after_frame')
        self.assertIsNone(rows[0]['before'])


class TargetInputTests(unittest.TestCase):
    def test_other_window_never_queries_configured_keys(self):
        game=WindowsGame.__new__(WindowsGame);game.hwnd=10;game.ready=Mock();game.foreground=Mock(return_value=20)
        game.u=SimpleNamespace(IsChild=Mock(return_value=False),GetAsyncKeyState=Mock(return_value=0x8000))
        self.assertFalse(game.human_input_snapshot(['w'])['target_focused']);game.u.GetAsyncKeyState.assert_not_called()
        game.foreground=Mock(side_effect=[10,20])
        self.assertEqual(game.human_input_snapshot(['w'])['keys'],[])
        game.foreground=Mock(return_value=10);game.u.GetKeyState=Mock(return_value=0)
        self.assertIn('recording_blocked',game.human_input_snapshot(['num1']))
        game.u.GetKeyState.return_value=1
        self.assertEqual(game.human_input_snapshot(['num1'])['keys'],['num1'])


class DemonstrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.cfg=Settings();self.path=Path(self.temp.name)/'memory.sqlite';self.memory=MemoryStore(self.path,self.cfg);self.addCleanup(self.memory.close)
        self.store=DemonstrationStore(self.memory,self.cfg)
        self.f={'kind':'main_menu','ocr_status':'ok','tokens':['道具','マッ','ップ'],'visual':[100]*192,'center':[90]*192,'contacts':None,'hp':None}

    def example(self,f=None,steps=None,status='complete'):
        return self.store.add({'complete':True,'before_features':f or self.f,'after_features':f or self.f,
            'steps':steps or [{'keys':['num1'],'duration_ms':100}],'effect':{'hp_delta':0}},status)

    def test_retrieval_is_profile_bound_censored_excluded_and_timings_bounded(self):
        identifier=self.example(steps=[{'keys':['num1'],'duration_ms':900}]);self.example(status='censored')
        with patch('autoplayer.demonstrations.features',return_value=self.f):
            found=self.store.retrieve(b'capture',ocr())
            self.assertEqual([e['id'] for e in found],[identifier]);self.assertEqual(found[0]['steps'][0]['hold_ms'],self.cfg.max_hold_ms)
            other_cfg=Settings(key_bindings={'w':'上移動'},allowed_keys=['w'])
            other=MemoryStore(self.path,other_cfg)
            try:self.assertEqual(DemonstrationStore(other,other_cfg).retrieve(b'capture',ocr()),[])
            finally:other.close()

    def test_orientation_example_rotates_only_for_cardinal_single_enemy(self):
        before=copy.deepcopy(self.f);before.update(kind='gameplay',contacts=[[0,-1]])
        identifier=self.example(before,[{'keys':['num5','w'],'duration_ms':80},{'keys':['num1'],'duration_ms':80}])
        current=copy.deepcopy(before);current['contacts']=[[1,0]]
        with patch('autoplayer.demonstrations.features',return_value=current):found=self.store.retrieve(b'capture',ocr())
        self.assertEqual(found[0]['id'],identifier);self.assertEqual(found[0]['steps'][0]['keys'],['num5','d'])
        self.assertTrue(found[0]['adaptation'])
        raw=json.loads(self.memory.db.execute('SELECT data FROM human_examples WHERE id=?',(identifier,)).fetchone()[0])
        self.assertEqual(raw['steps'][0]['keys'],['num5','w'])
        current['contacts']=[[1,1]]
        with patch('autoplayer.demonstrations.features',return_value=current):self.assertEqual(self.store.retrieve(b'capture',ocr()),[])

    def test_current_ocr_must_match_frame_and_private_observation_key_is_not_reused(self):
        self.assertEqual(features(b'new',ocr(b'old'))['ocr_status'],'frame_mismatch')
        self.example(steps=[{'keys':['num7'],'duration_ms':80}])
        with patch('autoplayer.demonstrations.features',return_value=self.f):self.assertEqual(self.store.retrieve(b'capture',ocr()),[])

    def test_async_processing_completes_raw_example_without_input_injection(self):
        backend=SimpleNamespace(act=Mock(side_effect=AssertionError('No injection')),release_all=Mock(side_effect=AssertionError('No injection')))
        control=SessionControl();recorder=Mock();reader=SimpleNamespace(read=Mock(side_effect=lambda png:ocr(png)))
        runner=DemonstrationRecorder(backend,self.store,reader,control,lambda _:None,Path(self.temp.name)/'run.jsonl',recorder)
        runner.save({'started':0.,'before':frame(0,.01),'after':frame(.4,.42),'events':[{'key':'num1','type':'down','at_ms':0},{'key':'num1','type':'up','at_ms':80}],
            'complete':True,'reason':'','duration_ms':500,'steps':[{'keys':['num1'],'duration_ms':80}]})
        runner.done.set();runner.processing_loop();runner.collect()
        self.assertEqual(self.memory.db.execute('SELECT status FROM human_examples').fetchone()[0],'complete')
        self.assertEqual(runner.stats['complete'],1);backend.act.assert_not_called();backend.release_all.assert_not_called()

    def test_blocked_capture_does_not_block_key_sampling(self):
        gate=threading.Event();control=SessionControl();calls=[]
        def capture():gate.wait(1);return b'capture-contract'
        def snapshot(allowed):
            calls.append(allowed)
            if len(calls)>=5:control.stop.set()
            return {'target_focused':False,'keys':[]}
        backend=SimpleNamespace(capture=capture,human_input_snapshot=snapshot,act=Mock(side_effect=AssertionError('No injection')))
        runner=DemonstrationRecorder(backend,self.store,SimpleNamespace(),control,lambda _:None,Path(self.temp.name)/'run.jsonl',Mock())
        started=time.monotonic()
        try:runner.run()
        finally:gate.set();control.close()
        self.assertGreaterEqual(len(calls),5);self.assertLess(time.monotonic()-started,.6);backend.act.assert_not_called()

    def test_planner_receives_example_and_records_it_as_a_suggestion(self):
        identifier=self.example();client=LocalLLM(self.cfg);client.demonstrations=self.store
        client.ocr=SimpleNamespace(read=Mock(return_value=ocr()))
        client.observe_scene=Mock(return_value={'scene_type':'menu','visible_text':['道具','マップ'],'description':'上段に道具とマップ、下段に足元とその他が表示されている。','player_position':'背景の中央','open_routes':[],'obstacles':[],'hazards':[],'confidence':.9})
        client._request=Mock(return_value=json.dumps(change(scene='menu',action={'keys':['num2'],'hold_ms':80,'wait_ms':0},observation='上段に道具とマップ、下段に足元とその他が表示されている。',subgoal='メニューを閉じて出口探索に戻る',reason='ダンジョン外のメニューは探索候補にせず取消する。',goal_success='メニューが閉じたことを次に確認する。')))
        with patch('autoplayer.demonstrations.features',return_value=self.f):client.decide(b'capture-contract',[],False)
        prompt=client._request.call_args.args[2]
        self.assertEqual(prompt['human_examples'][0]['id'],identifier)
        self.assertEqual(prompt['allowed_keys'],['num2'])
        self.assertEqual(prompt['exterior_action_policy']['mode'],'close_existing_menu')
        self.assertEqual(client.take_state()['demonstration_candidates'][0]['id'],identifier)
        self.store.record_reuse({'demonstration_candidates':client.take_state()['demonstration_candidates'],'executed':False,'observed_keys':[]})
        reused=json.loads(self.memory.db.execute('SELECT data FROM human_reuse').fetchone()[0])
        self.assertFalse(reused['executed']);self.assertEqual(reused['matches_first_step'],[])

class RecordingRevisionTests(unittest.TestCase):
    setUp=DemonstrationTests.setUp
    example=DemonstrationTests.example
    def test_hud_identity_does_not_invent_missing_hp_and_reclassifies_legacy(self):
        png=b'capture-contract';native=ocr(png)
        native['lines']=[{'text':t} for t in ['HP1S','1階','Lv1','満腹度10000','0G']]
        result=features(png,native)
        self.assertEqual(result['kind'],'gameplay');self.assertIsNone(result['hp'])
        old=copy.deepcopy(self.f);old.update(kind='unknown',quotes=['HP1S','1階','Lv1','満腹度10000','0G'],hp=None)
        identifier=self.example(old)
        with patch('autoplayer.demonstrations.features',return_value=old):found=self.store.retrieve(png,native)
        self.assertEqual(found[0]['id'],identifier);self.assertIsNone(found[0]['current_hp'])
        native['lines'].append({'text':'はい'})
        self.assertEqual(features(png,native)['kind'],'choice')

    def test_visual_reference_needs_same_profile_and_rejects_choice(self):
        anchor=copy.deepcopy(self.f);anchor.update(kind='gameplay',hud=[100]*128,quotes=['1階','Lv1','満腹度99%'])
        self.example(anchor)
        current=copy.deepcopy(anchor);current.update(kind='unknown',tokens=[],quotes=[],hp=None)
        with patch('autoplayer.demonstrations.features',return_value=current):
            self.assertTrue(self.store.retrieve(b'capture',ocr()))
            self.assertEqual(self.store.last_search['current_kind'],'gameplay')
        current['hud']=[0]*128
        with patch('autoplayer.demonstrations.features',return_value=current):self.assertEqual(self.store.retrieve(b'capture',ocr()),[])
        current.update(kind='choice',hud=[100]*128)
        with patch('autoplayer.demonstrations.features',return_value=current):self.assertEqual(self.store.retrieve(b'capture',ocr()),[])

    def test_observed_bad_first_action_lowers_priority_but_unsent_does_not(self):
        first=self.example();second=self.example()
        with patch('autoplayer.demonstrations.features',return_value=self.f):candidates=self.store.retrieve(b'capture',ocr())
        example=next(e for e in candidates if e['id']==second)
        row={'demonstration_candidates':[example],'executed':True,'action_kind':'input','observed_keys':['num1'],
             'hold_ms':example['steps'][0]['hold_ms'],'input_receipt':{'os_submission':'accepted'},'turn_kind':'stable',
             'policy_learning':{'status':'trained','reward':-.5}}
        for _ in range(3):self.store.record_reuse(row)
        with patch('autoplayer.demonstrations.features',return_value=self.f):found=self.store.retrieve(b'capture',ocr())
        self.assertEqual(found[0]['id'],first);self.assertEqual(found[1]['reuse_experience']['trials'],3)
        row['executed']=False;self.store.record_reuse(row)
        self.assertEqual(self.memory.db.execute('SELECT trials FROM human_example_scores').fetchone()[0],3)
        row.update(executed=True,observed_keys=['w']);self.store.record_reuse(row)
        self.assertEqual(self.memory.db.execute('SELECT trials FROM human_example_scores').fetchone()[0],3)

    def test_slow_database_consumer_does_not_stop_input_sampler(self):
        control=SessionControl();calls=[];gate=threading.Event()
        def snapshot(allowed):
            calls.append(time.monotonic())
            if len(calls)>=12:control.stop.set()
            return {'target_focused':False,'keys':[]}
        backend=SimpleNamespace(capture=lambda:b'capture-contract',human_input_snapshot=snapshot)
        runner=DemonstrationRecorder(backend,self.store,SimpleNamespace(),control,lambda _:None,Path(self.temp.name)/'run.jsonl',Mock())
        original=runner.write
        def slow_write(row):
            gate.set();time.sleep(.3);original(row)
        runner.write=slow_write
        try:stats=runner.run()
        finally:control.close()
        self.assertTrue(gate.is_set());self.assertGreaterEqual(len(calls),12)
        self.assertLess(calls[-1]-calls[0],.29)
        self.assertIn('sample_gap_max_ms',stats);self.assertGreater(stats['sample_count'],0)

    def test_bounded_export_covers_all_examples_without_images_or_large_database(self):
        import gzip,zipfile
        from autoplayer.diagnostics import export_diagnostics,export_human_examples
        for _ in range(75):self.example()
        self.memory.db.execute('CREATE TABLE bulky(data BLOB)');self.memory.db.execute('INSERT INTO bulky VALUES(zeroblob(7000000))');self.memory.db.commit()
        archive=Path(self.temp.name)/'report.zip';report=export_diagnostics(self.temp.name,archive)
        entry=report['databases']['memory.sqlite'];export=entry['human_example_export']
        self.assertFalse(entry['snapshot_included']);self.assertEqual(export['included'],75);self.assertFalse(export['truncated'])
        with zipfile.ZipFile(archive) as z:
            lines=gzip.decompress(z.read(export['path'])).splitlines()
        self.assertEqual(len(lines),75);self.assertEqual(json.loads(lines[0])['id'],75)
        with zipfile.ZipFile(Path(self.temp.name)/'bounded.zip','w') as z:
            small=export_human_examples(self.memory.db,z,self.temp.name,limit_bytes=500)
        self.assertTrue(small['truncated']);self.assertLessEqual(small['compressed_bytes'],500)
