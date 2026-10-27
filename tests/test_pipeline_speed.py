"""Recognizer/geometry/timing/queue contracts; no game simulation."""
import copy,tempfile,threading,unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from autoplayer.recognition_cache import CroppedRecognitionCache
from autoplayer.map_sampling import MapSamplingPlan
from autoplayer.observation import TurnObserver
from autoplayer.recording import FrameRecorder
from autoplayer.core import Controller,Settings
from autoplayer.memory import MemoryStore
from test_agent import Backend,Script

@dataclass
class Pixels:
    data:bytes
    shape:tuple=(2,2,3)
    dtype:str='uint8'
    def tobytes(self):return self.data
@dataclass
class Input:
    img:list
    return_word_box:bool=False
@dataclass
class Output:
    imgs:list
    txts:tuple
    scores:tuple
    word_results:list
    elapse:float=.1
class Recognizer:
    def __init__(self):self.batches=[]
    def __call__(self,args):
        self.batches.append(args.img)
        return Output(args.img,tuple(i.data.decode() for i in args.img),tuple(.9 for i in args.img),[])

class PipelineSpeedTests(unittest.TestCase):
    def test_exact_crop_reuse_preserves_order_and_only_changed_pixels_are_recognized(self):
        r=Recognizer();c=CroppedRecognitionCache(r);c.enabled=True
        c(Input([Pixels(b'a'),Pixels(b'b')]))
        self.assertEqual(c(Input([Pixels(b'b'),Pixels(b'a')])).txts,('b','a'));self.assertEqual(c.hits,2);self.assertEqual(len(r.batches),1)
        self.assertEqual(c(Input([Pixels(b'b'),Pixels(b'c')])).txts,('b','c'));self.assertEqual(len(r.batches[-1]),1)
        c(Input([Pixels(b'c',shape=(1,4,3))]));self.assertEqual(c.misses,1)
    def test_force_fresh_crops_change_confirmation_tokens_and_bypass_mode_is_unchanged(self):
        r=Recognizer();c=CroppedRecognitionCache(r);c.enabled=True
        with patch('autoplayer.recognition_cache.time.time',side_effect=[10.,20.]):
            c(Input([Pixels(b'a')]));c(Input([Pixels(b'a')]))
            self.assertEqual(c.last_records[0][2],10.)
            c.force_fresh=True;c(Input([Pixels(b'a')]))
            self.assertEqual(c.last_records[0][2],20.);self.assertEqual(c.hits,0)
        c.enabled=False;c(Input([Pixels(b'a')]));self.assertEqual(c.misses,1)
    def test_map_checks_are_spaced_and_damage_or_floor_change_requests_recheck(self):
        p=MapSamplingPlan();s={'metrics':{'floor':{'values':[1]},'hp':{'values':[15,15]}},'minimap':{'status':'observed','player_cell':[3,3]}}
        self.assertTrue(p.due(s,0,'map_observation'));p.confirmed(s,0)
        self.assertFalse(p.due(s,0,'map_result'));self.assertFalse(p.due(s,3,'map_observation'));self.assertTrue(p.due(s,4,'map_observation'))
        hurt=copy.deepcopy(s);hurt['metrics']['hp']['values']=[14,15];self.assertTrue(p.due(hurt,1,'map_result'))
        p.after_input(['w'],s);self.assertEqual(p.context()['estimated_player_cell'],[3,2]);self.assertEqual(s['minimap']['player_cell'],[3,3])
        self.assertEqual(p.context()['status'],'input_based_unverified')
        wrong=copy.deepcopy(s);wrong['minimap']['player_cell']=[3,3];p.confirmed(wrong,4)
        self.assertTrue(p.due(wrong,5,'map_observation'));self.assertEqual(p.context()['reconciliation']['inputs'],[['w']])
        p.after_input(['num1'],s);self.assertIsNone(p.context()['estimated_player_cell'])
    def test_fast_map_display_reuses_explicit_normal_metrics_without_ocr(self):
        with tempfile.TemporaryDirectory() as t:
            cfg=Settings(fast_learning=True);memory=MemoryStore(Path(t)/'m.sqlite',cfg)
            try:
                backend=Backend();backend.capture_map_display=Mock(return_value={'status':'captured','image':b'map','restored_image':b'normal'})
                llm=Script([]);llm.ocr=SimpleNamespace(read=Mock(side_effect=AssertionError('No repeated map OCR')))
                a=Controller(cfg,backend,llm,lambda _:None,Path(t)/'run',memory=memory)
                normal=a.policy.observe(b'normal',{'status':'ok','lines':[{'text':'1階 HP15/15'}]})
                m={'status':'observed','sensor':'fixed_grid_54x32','player_cell':[3,3],'known_cells':[(0,0)],'enemies':[],'items':[],'stairs':[]}
                with patch('autoplayer.policy.read_grid',return_value=m):result=a.map_sample(b'normal',normal,'map_observation')[0]
                self.assertEqual(result['metrics']['hp']['values'],[15,15]);self.assertEqual(result['metrics_source'],'normal_capture_before_map_display')
                self.assertNotEqual(result['metrics_source_frame_sha256'],result['frame_sha256'])
                self.assertIsNone(a.map_sample(b'normal',normal,'map_result'));self.assertEqual(backend.capture_map_display.call_count,1)
                # A pending tactical continuation requires a new current contact,
                # even when the normal map interval has not elapsed.
                a.combat.pending={'stage':'orient_trial'}
                with patch('autoplayer.policy.read_grid',return_value=m):
                    self.assertIsNotNone(a.map_sample(b'normal',normal,'map_observation'))
                self.assertEqual(backend.capture_map_display.call_count,2)
                menu=copy.deepcopy(normal);menu['menu_evidence']=True
                self.assertIsNone(a.map_sample(b'normal',menu,'map_observation'))
                self.assertEqual(backend.capture_map_display.call_count,2)
                llm.ocr.read.assert_not_called()
            finally:memory.close()
    def test_neutral_sampling_is_short_but_retains_periodic_reference_window(self):
        class Clock:
            def __init__(self):self.now=0.
            def __call__(self):return self.now
            def wait(self,d):self.now+=d;return False
            def is_set(self):return False
        c=Clock();o=TurnObserver();o.fast_mode=True;o.grid=lambda frame:frame
        durations=[o.observe(lambda:[0]*768,[0]*768,'neutral_observation',c,clock=c).duration_ms for _ in range(8)]
        self.assertTrue(all(d<500 for d in durations[:7]));self.assertGreaterEqual(durations[-1],1200)
    def test_timing_shortens_only_after_observed_samples_and_keeps_a_margin(self):
        class Clock:
            def __init__(self):self.now=0.
            def __call__(self):return self.now
            def wait(self,d):self.now+=d;return False
            def is_set(self):return False
        c=Clock();o=TurnObserver();o.fast_mode=True;o.input_signature=(('w',),0);o.grid=lambda frame:frame
        results=[o.observe(lambda:[255]*768,[0]*768,'unknown',c,600,clock=c) for _ in range(5)]
        self.assertEqual(results[0].applied_minimum_ms,600);self.assertLess(results[-1].applied_minimum_ms,600);self.assertGreaterEqual(results[-1].applied_minimum_ms,350)
        new_context=o.observe(lambda:[255]*768,[128]*768,'unknown',c,600,clock=c)
        self.assertEqual(new_context.applied_minimum_ms,600)
    def test_important_image_processing_runs_on_recorder_worker_and_copies_row(self):
        with tempfile.TemporaryDirectory() as t:
            recorder=FrameRecorder(Path(t));seen=[]
            recorder.important.observe=lambda row,*_:seen.append((row['step'],threading.get_ident()))
            row={'step':1};recorder.record_important(row,b'before',b'after');row['step']=9;recorder.close()
            self.assertEqual(seen[0][0],1);self.assertNotEqual(seen[0][1],threading.get_ident());self.assertEqual(recorder.failed,0)

    def test_learning_updates_commit_once_and_roll_back_on_error(self):
        with tempfile.TemporaryDirectory() as t:
            memory=MemoryStore(Path(t)/'m.sqlite',Settings());memory.db.execute('CREATE TABLE batch_test(value INTEGER)');trace=[];memory.db.set_trace_callback(trace.append)
            try:
                with memory.learning_batch():
                    memory.db.execute('INSERT INTO batch_test VALUES(1)');memory.db.commit()
                    with memory.db:memory.db.execute('INSERT INTO batch_test VALUES(2)')
                    self.assertNotIn('COMMIT',trace)
                self.assertEqual(trace.count('COMMIT'),1)
                with self.assertRaises(ValueError):
                    with memory.learning_batch():memory.db.execute('INSERT INTO batch_test VALUES(3)');raise ValueError('invalid feedback')
                self.assertEqual(memory.db.execute('SELECT value FROM batch_test').fetchall(),[(1,),(2,)])
                self.assertEqual(memory.db.batch_depth,0)
            finally:memory.close()
