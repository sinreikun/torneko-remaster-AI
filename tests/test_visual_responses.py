"""Image-response/backoff contracts; no simulated game or gameplay claims."""
import io,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from PIL import Image
from autoplayer.visual_responses import VisualResponses
from autoplayer.core import Settings,Controller,parse_decision,response_quality_issue
from autoplayer.memory import MemoryStore
from autoplayer.active_input import ActiveInput
from test_agent import Backend,Script,change

A=[0]*192
B=[7]*30+[0]*162
C=[0]*30+[7]*30+[0]*132

def receipt(keys):return {'executed':True,'action_kind':'input','input_receipt':{'os_submission':'accepted','events':[{'message':'WM_KEYDOWN'}]},'observed_keys':keys}
class VisualResponseTests(unittest.TestCase):
    def test_small_animation_does_not_clear_repeated_action_backoff(self):
        model=VisualResponses()
        self.assertNotEqual(model.scope,VisualResponses().scope)
        for step in range(3):result=model.record(A,[1]*192,A,receipt(['w','a']),step)
        self.assertFalse(result['context_change']);self.assertEqual(result['stagnant_turns'],3)
        self.assertEqual(model.evidence(A,3)['blocked_actions'],[['w','a']])
        d=parse_decision(json.dumps(change(action={'keys':['w','a'],'hold_ms':80,'wait_ms':0})),Settings())
        selected,_=ActiveInput(Settings()).select(d,blocked=model.evidence(A,3)['blocked_actions'],preserve_proposal=True)
        self.assertNotEqual(selected.keys,['w','a']);self.assertTrue(selected.keys)
    def test_returning_to_context_preserves_backoff_but_cooldown_allows_retry(self):
        model=VisualResponses()
        for step in range(3):model.record(A,A,A,receipt(['w','a']),step)
        self.assertTrue(model.record(A,B,A,receipt(['s']),3)['context_change'])
        model.record(B,A,B,receipt(['w']),4)
        self.assertEqual(model.evidence(A,5)['blocked_actions'],[['w','a']])
        self.assertEqual(model.evidence(A,14)['blocked_actions'],[])
    def test_natural_activity_is_not_attributed_to_input(self):
        result=VisualResponses().record(B,C,C,receipt(['d']),0)
        self.assertFalse(result['context_change']);self.assertGreater(result['neutral_activity'],0)
    def test_diagnostics_missing_images_and_unaccepted_inputs_do_not_train(self):
        model=VisualResponses()
        for patch in ({'event':'diagnostic'},{'executed':False},{'input_receipt':{}},{'turn_kind':'aborted'},{'dry_run':True}):
            self.assertEqual(model.record(A,B,A,dict(receipt(['s']),**patch),0)['status'],'excluded')
        self.assertEqual(model.record(A,None,A,receipt(['s']),0)['status'],'missing_image_pair');self.assertEqual(model.actions,{})
    def test_anchor_and_action_storage_stay_bounded_when_old_origin_is_evicted(self):
        model=VisualResponses(capacity=1)
        for step,grid in enumerate((B,C,A,B,C)):
            model.record(A,grid,A,receipt(['s']),step)
            self.assertLessEqual(len(model.anchors),1);self.assertTrue(set(model.actions)<=set(model.anchors))
    def test_goal_copy_is_not_a_current_action_reason(self):
        cfg=Settings();d=parse_decision(json.dumps(change(reason=cfg.goal)),cfg)
        self.assertTrue(response_quality_issue(d,(cfg.goal,)))
        d=parse_decision(json.dumps(change(reason='左の通路候補を短く試す')),cfg)
        self.assertFalse(response_quality_issue(d,(cfg.goal,)))
    def test_changed_frame_during_inference_is_not_an_input_effect_sample(self):
        def png(color):
            output=io.BytesIO();Image.new('RGB',(64,64),color).save(output,format='PNG');return output.getvalue()
        first,latest=png((20,20,20)),png((220,220,220));cfg=Settings(max_steps=1,fast_learning=True)
        script=Script([change()]);script.ocr=SimpleNamespace(read=Mock(return_value={'status':'ok','lines':[]}))
        class Receipt(Backend):
            def __init__(self):super().__init__();self.captures=0
            def capture(self):self.captures+=1;return first if self.captures==1 else latest
            def is_current(self,image):return False
            def act(self,*args):super().act(*args);return receipt(args[0])['input_receipt']
        backend=Receipt()
        def observe(*args):return SimpleNamespace(image=args[0] if args[1]=='neutral_observation' else latest,kind='normal',changed=False,raw_change=0,masked_activity=0,duration_ms=500)
        backend.observe_turn=Mock(side_effect=observe)
        with tempfile.TemporaryDirectory() as t:
            memory=MemoryStore(Path(t)/'memory.sqlite',cfg)
            try:
                agent=Controller(cfg,backend,script,lambda _:None,Path(t)/'run',memory=memory);agent.run()
                rows=[json.loads(l) for l in agent.journal.read_text().splitlines() if json.loads(l).get('event')=='result']
            finally:memory.close()
        self.assertEqual(rows[0]['visual_response']['reason'],'frame_changed_during_inference')
        self.assertFalse(rows[0]['visual_fallback']['fresh_model_input_retained'])
    def test_dispatch_preserves_backoff_even_when_raw_observer_reports_change(self):
        output=io.BytesIO();Image.new('RGB',(64,64),(70,100,130)).save(output,format='PNG');png=output.getvalue()
        cfg=Settings(max_steps=4,fast_learning=True)
        script=Script([change(action={'keys':['w','a'],'hold_ms':80,'wait_ms':0})]*4);script.ocr=SimpleNamespace(read=Mock(return_value={'status':'ok','lines':[]}))
        class Receipt(Backend):
            def capture(self):return png
            def act(self,*args):super().act(*args);return receipt(args[0])['input_receipt']
        backend=Receipt()
        def observe(*args):return SimpleNamespace(image=png,kind='normal',changed=True,raw_change=0 if args[1]=='neutral_observation' else .06,masked_activity=.01,duration_ms=500)
        backend.observe_turn=Mock(side_effect=observe)
        with tempfile.TemporaryDirectory() as t:
            memory=MemoryStore(Path(t)/'memory.sqlite',cfg)
            try:
                agent=Controller(cfg,backend,script,lambda _:None,Path(t)/'run',memory=memory);agent.run()
                rows=[json.loads(l) for l in agent.journal.read_text().splitlines() if json.loads(l).get('event')=='result']
            finally:memory.close()
        self.assertEqual([a[0] for a in backend.actions[:3]],[['w','a']]*3);self.assertNotEqual(backend.actions[3][0],['w','a'])
        self.assertTrue(rows[0]['raw_recovery_context_change']);self.assertFalse(rows[0]['recovery_context_change'])
        self.assertTrue(script.calls[-1][2]);self.assertFalse(rows[-1]['visual_fallback']['fresh_model_input_retained'])
