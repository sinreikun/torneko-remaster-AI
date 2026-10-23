"""Payload and bounded scheduling contracts, not game throughput tests."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from autoplayer.core import Settings,Controller
from autoplayer.memory import MemoryStore
from autoplayer.performance import ocr_context,knowledge_context,fast_policy_ready
from test_policy import state
from test_agent import Script,Backend,change

class PerformanceTests(unittest.TestCase):
    def test_prompt_drops_ocr_geometry_and_duplicate_passes_only(self):
        native={'status':'ok','frame_sha256':'frame','lines':[{'text':'HP 15/15','words':[{'text':'HP','box':[1,2,3,4]}]}],
            'full_frame_lines':[{'text':'HP -15/15'}],'top_strip_lines':[{'text':'HP 15/15'}]}
        compact=ocr_context(native)
        self.assertEqual(compact['lines'],[{'text':'HP 15/15'}]);self.assertEqual(compact['frame_sha256'],'frame')
        self.assertNotIn('words',json.dumps(compact));self.assertNotIn('full_frame_lines',compact)
        self.assertTrue(native['lines'][0]['words'])
    def test_knowledge_keeps_source_identity_and_uncertainty_without_duplicate_ocr(self):
        from autoplayer.knowledge import KnowledgeEngine
        original=KnowledgeEngine().reconcile({'status':'ok','lines':[{'text':'HP 15/15','words':[{'box':[1,2,3,4]}]}]})
        slim=knowledge_context(original)
        self.assertEqual(slim['metrics'],original['metrics']);self.assertEqual(slim['edition'],original['edition'])
        self.assertTrue(slim['sources']);self.assertEqual(len(slim['facts']),6)
        self.assertTrue(all('applicability' in f for f in slim['facts']))
        self.assertLess(len(json.dumps(slim)),len(json.dumps(original)))
    def test_fast_path_requires_current_safe_observations_and_periodic_model(self):
        args=[state(),True,object(),[1],'explore',0,[],0,False]
        self.assertTrue(fast_policy_ready(*args))
        for index,value in ((1,False),(2,None),(3,[2]),(4,'forage'),(5,3),(6,[['w']]),(7,2),(8,True)):
            values=args.copy();values[index]=value;self.assertFalse(fast_policy_ready(*values))
        for mutate in ('enemy','missing','survive'):
            values=args.copy();values[0]=copy.deepcopy(args[0])
            if mutate=='enemy':values[0]['minimap']['enemies']=[(5,5)]
            elif mutate=='missing':values[0]['eligible']=False
            else:values[0]['objective']='survive';values[4]='survive'
            self.assertFalse(fast_policy_ready(*values))
    def test_controller_replans_when_current_observation_cannot_verify_prediction(self):
        self.run_bounded_controller(False)
    def test_valid_dedicated_map_can_continue_without_adopting_invalid_model_report(self):
        self.run_bounded_controller(True)
    def run_bounded_controller(self,dedicated):
        class ReceiptBackend(Backend):
            def act(self,keys,ms,stop):
                super().act(keys,ms,stop)
                return {'os_submission':'accepted','events':[{'message':'WM_KEYDOWN'}]}
        backend=ReceiptBackend()
        def observation(*args):return SimpleNamespace(image=b'current',kind='normal',changed=True,raw_change=.3 if len(args)>1 and args[1]!='neutral_observation' else 0,masked_activity=0,duration_ms=1000)
        backend.observe_turn=Mock(side_effect=observation)
        script=Script([change(scene='gameplay',action={'keys':[],'hold_ms':1,'wait_ms':0})]*5)
        script.ocr=SimpleNamespace(read=Mock(return_value={'status':'ok','lines':[]}))
        script.take_state=lambda:{'scene_observation':{'status':'model_observation_unverified'},**({'learning':False} if dedicated else {})}
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Settings(max_steps=5);store=MemoryStore(Path(tmp)/'memory.sqlite',cfg)
            try:
                agent=Controller(cfg,backend,script,lambda r:None,Path(tmp)/'run.jsonl',memory=store)
                agent.policy.observe=Mock(side_effect=lambda *args:{**state(),**({'observation_mode':'num7_held'} if dedicated else {})})
                agent.run()
                results=[json.loads(l) for l in agent.journal.read_text().splitlines() if json.loads(l)['event']=='result']
            finally:store.close()
        if dedicated:self.assertTrue(all(not r['model_report_learning'] for r in results))
        self.assertEqual(len(script.calls),5);self.assertEqual(len(backend.actions),5)
        self.assertEqual([r['performance']['llm_requested'] for r in results],[True,True,True,True,True])
        self.assertGreater(len({tuple(r['observed_keys']) for r in results[:4]}),1)
        self.assertTrue(all(r['policy_learning']['status']=='trained' for r in results))
        self.assertTrue(all(r['forecast_evaluation']['status']!='matched' for r in results))
