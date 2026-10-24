"""Scheduling, dispatch and evidence contracts; no simulated game."""
import copy,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from autoplayer.core import Settings,Controller
from autoplayer.memory import MemoryStore
from autoplayer.fast_learning import FastLearningGate
from autoplayer.experience_review import ExperienceReview
from test_agent import Backend,Script,change

EMPTY={'status':'ok','lines':[]}
def image_state():return {'visual':[0]*192,'frame_sha256':'a'*64,'observable':False,'eligible':False,'text_key':'empty','text_reliable':False,'metrics':{},'minimap':{'status':'unavailable'},'id':'image','location':'image','objective':'interface'}

class VisualFallbackTests(unittest.TestCase):
    def test_missing_text_and_map_requests_current_image_every_turn_then_recovers(self):
        gate=FastLearningGate(128);state=image_state()
        for turn in range(3):
            result=gate.evaluate(turn,EMPTY,state,{})
            self.assertTrue(result['llm_requested']);self.assertTrue(result['visual_fallback']);self.assertEqual(result['next_review_in'],0)
        self.assertEqual(result['reason'],'visual_evidence_gap')
        restored=gate.evaluate(3,{'status':'ok','lines':[{'text':'HP15/15'}]},{**state,'metrics':{'hp':{'values':[15,15]}}},{})
        self.assertFalse(restored['llm_requested']);self.assertFalse(restored['visual_fallback'])
    def test_capture_without_game_specific_policy_also_requests_visual_review(self):
        gate=FastLearningGate(128);gate.evaluate(0,EMPTY,{}, {},image_available=True)
        self.assertEqual(gate.evaluate(1,EMPTY,{}, {},image_available=True)['reason'],'visual_evidence_gap')
    def test_current_map_does_not_force_visual_fallback(self):
        state=image_state();state.update(eligible=True,minimap={'status':'observed'})
        gate=FastLearningGate(128);gate.evaluate(0,EMPTY,state,{})
        self.assertFalse(gate.evaluate(1,EMPTY,state,{})['llm_requested'])
    def test_image_pairs_record_measurements_without_goal_reward_or_text_loop(self):
        review=ExperienceReview();row={'executed':True,'action_kind':'input','input_receipt':{'os_submission':'accepted'},'observed_keys':['d'],'raw_change':.2,'neutral_before':{'raw_change':.1},'policy_learning':{'reward':1.0}}
        row.update(learning=False,visual_fallback={'active':True})
        result=review.record(row,image_state(),image_state())
        self.assertEqual(result['evidence_mode'],'image_pair_only');self.assertIsNone(review.rows[-1]['reward_proxy']);self.assertFalse(review.rows[-1]['nonspatial'])
        self.assertEqual(review.rows[-1]['raw_activity'],.2);self.assertEqual(review.rows[-1]['neutral_activity'],.1)
        self.assertEqual(review.context()['image_pair_only_observations'],1)
        self.assertEqual(review.record(dict(row,event='diagnostic'),image_state(),image_state())['status'],'excluded')
        self.assertEqual(review.record(row,{},image_state())['status'],'missing_observation')
    def test_controller_keeps_fresh_visual_input_instead_of_coverage_override(self):
        cfg=Settings(allowed_keys=['w','a','s','d'],max_steps=2,fast_learning=True).validate()
        script=Script([change(action={'keys':[key],'hold_ms':80,'wait_ms':0}) for key in ('d','s')])
        script.ocr=SimpleNamespace(read=Mock(return_value=EMPTY))
        class Receipt(Backend):
            def act(self,*args):super().act(*args);return {'os_submission':'accepted','events':[{'message':'WM_KEYDOWN'}]}
        backend=Receipt();backend.observe_turn=Mock(return_value=SimpleNamespace(image=b'current',kind='normal',changed=False,raw_change=0,masked_activity=0,duration_ms=500))
        with tempfile.TemporaryDirectory() as t:
            memory=MemoryStore(Path(t)/'memory.sqlite',cfg)
            try:
                agent=Controller(cfg,backend,script,lambda _:None,Path(t)/'run',memory=memory)
                agent.policy.observe=Mock(side_effect=lambda *args,**kw:copy.deepcopy(image_state()))
                agent.run();rows=[json.loads(l) for l in agent.journal.read_text().splitlines() if json.loads(l).get('event')=='result']
            finally:memory.close()
        self.assertEqual(len(script.calls),2);self.assertEqual([a[0] for a in backend.actions],[['d'],['s']])
        self.assertTrue(all(r['visual_fallback']['fresh_model_input_retained'] for r in rows))
        self.assertTrue(all(r['experience_review']['evidence_mode']=='image_pair_only' for r in rows))
        self.assertTrue(all(r['policy_learning']['status']=='censored' for r in rows))
