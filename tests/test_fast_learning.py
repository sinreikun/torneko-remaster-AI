"""Inference scheduling and image compositing contracts, no demo game."""
import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from PIL import Image
from autoplayer.fast_learning import FastLearningGate
from autoplayer.dashboard import translucent_background
from autoplayer.core import Controller,Settings
from autoplayer.telemetry import RecognitionState
from test_agent import Backend,Script,change

EMPTY={'status':'ok','lines':[]}
class FastLearningTests(unittest.TestCase):
    def test_wide_interval_skips_normal_turns_then_forces_review(self):
        gate=FastLearningGate(128)
        self.assertTrue(gate.evaluate(0,EMPTY,{},{} )['llm_requested'])
        self.assertTrue(all(not gate.evaluate(n,EMPTY,{},{} )['llm_requested'] for n in range(1,128)))
        self.assertEqual(gate.evaluate(128,EMPTY,{},{} )['reason'],'periodic_review')
    def test_new_choice_and_guidance_trigger_review_but_same_choice_is_debounced(self):
        g=FastLearningGate(128);g.evaluate(0,EMPTY,{}, {'revision':0})
        choice={'status':'ok','lines':[{'text':'はい'},{'text':'いいえ'}]}
        self.assertEqual(g.evaluate(1,choice,{}, {'revision':0})['reason'],'choice_or_objective_event')
        self.assertFalse(g.evaluate(2,{**choice,'lines':choice['lines']+[{'text':'HP14/15'},{'text':'満腹度96%'}]}, {}, {'revision':0})['llm_requested'])
        self.assertEqual(g.evaluate(3,choice,{}, {'revision':1})['reason'],'operator_guidance_changed')
    def test_floor_and_acquisition_events_and_frame_link(self):
        g=FastLearningGate();state={'metrics':{'floor':{'values':[1]}}};g.evaluate(0,EMPTY,state,{})
        state['metrics']['floor']['values'][0]=2
        self.assertEqual(g.evaluate(1,EMPTY,state,{})['reason'],'confirmed_floor_changed')
        box={'status':'ok','lines':[{'text':'箱を手に入れた'}]}
        self.assertEqual(g.evaluate(2,box,state,{})['reason'],'choice_or_objective_event')
        stale={'status':'ok','frame_sha256':'old','lines':[{'text':'選択してください'}]}
        self.assertFalse(g.evaluate(3,stale,{**state,'frame_sha256':'new'},{} )['llm_requested'])
    def test_local_turns_do_not_request_llm_or_reuse_old_observer_reports(self):
        cfg=Settings(window_title='Other',allowed_keys=['a','b'],max_steps=6,fast_learning=True,llm_review_interval=128,goal='情報を集めて帰還する').validate()
        script=Script([change(action={'keys':['b'],'hold_ms':80,'wait_ms':0})])
        script.take_state=lambda:{'scene_observation':{'description':'過去の報告'}}
        backend=Backend();backend.observe_turn=Mock(return_value=SimpleNamespace(image=b'current',kind='normal',changed=False,raw_change=0,masked_activity=0,duration_ms=0))
        with tempfile.TemporaryDirectory() as t:
            from autoplayer.memory import MemoryStore
            memory=MemoryStore(Path(t)/'memory.sqlite',cfg)
            try:
                c=Controller(cfg,backend,script,lambda _:None,Path(t)/'run',memory=memory);c.run()
                rows=[json.loads(l) for l in c.journal.read_text().splitlines() if json.loads(l)['event']=='result']
                self.assertEqual(memory.db.batch_depth,0)
                self.assertIn('learning_batch_commit_ms',rows[-1]['performance']['stages_ms'])
            finally:memory.close()
        self.assertEqual(len(script.calls),1);self.assertEqual(len(backend.actions),6)
        self.assertEqual([r['performance']['llm_requested'] for r in rows],[True,False,False,False,False,False])
        self.assertTrue(all(r['scene_observation']['status']=='not_requested' for r in rows[1:]))
        self.assertTrue(all(not r['inference_metrics'] for r in rows[1:]))
        self.assertTrue(all(r.get('llm_proposal') is None for r in rows[1:]))
        self.assertEqual(cfg.goal,'情報を集めて帰還する')
        from autoplayer.dashboard import LearningTraces
        traces=LearningTraces()
        for row in rows:traces.update(row)
        self.assertIsNone(traces.samples[-1]['confidence'])
    def test_background_is_half_blended_and_source_image_is_untouched(self):
        source=Image.new('RGB',(10,10),(220,180,140));image=translucent_background(source,(2,3,4,5))
        self.assertEqual(image.size,(4,5));pixel=image.getpixel((0,0))
        self.assertTrue(all(low<v<high for v,low,high in zip(pixel,(17,26,41),(220,180,140))))
        self.assertEqual(source.getpixel((2,3)),(220,180,140))
        self.assertEqual(translucent_background(source,(0,0,2,2),0).getpixel((0,0)),(220,180,140))
        self.assertEqual(translucent_background(source,(0,0,2,2),1).getpixel((0,0)),(17,26,41))
    def test_learning_counter_is_not_a_floor_prediction_counter(self):
        state=RecognitionState();state.update({'event':'result','policy_after':{'metrics':{},'minimap':{},'floor_confirmation':{'status':'unreadable'}},'fast_learning':{'next_review_in':127}})
        self.assertEqual(state.remaining,127)
    def test_invalid_intervals_are_rejected(self):
        for value in (0,15,4097,True):
            with self.assertRaises(ValueError):Settings(fast_learning=True,llm_review_interval=value).validate()
