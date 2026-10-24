"""Numerical prediction and control contracts; no game simulation."""
import copy,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from autoplayer.anticipation import Anticipation,validate_forecast
from autoplayer.core import Settings,parse_decision,Controller
from autoplayer.memory import MemoryStore
from test_agent import change,Script,Backend
from test_policy import state

WORLD={'novelty':0,'new_objects':[],'scene_change':False,'new_messages':[]}
OCR={'status':'ok','lines':[]}

def proposal():return parse_decision(json.dumps(change(scene='gameplay',action={'keys':['d'],'hold_ms':80,'wait_ms':0},forecast={
    'horizon':8,'first_expected':{'player_delta':[1,0]},'continuations':[{'keys':['d'],'hold_ms':80,'wait_ms':0,'expected':{'player_delta':[1,0]}}]*7})),Settings())
def receipt():return {'executed':True,'turn_kind':'normal','input_receipt':{'os_submission':'accepted'}}

class ForecastTests(unittest.TestCase):
    def test_invalid_future_action_is_discarded_without_losing_valid_first_action(self):
        value=change(forecast={'horizon':8,'continuations':[{'keys':['enter'],'hold_ms':99999,'wait_ms':0,'expected':{}}]})
        d=parse_decision(json.dumps(value),Settings());self.assertEqual(d.keys,['num1']);self.assertEqual(d.forecast['horizon'],1)
        for keys in ([{}],['w','s'],['d','d']):
            with self.assertRaises(ValueError):validate_forecast({'continuations':[{'keys':keys,'hold_ms':80,'wait_ms':0,'expected':{'hp_min':1}}]},Settings())
    def test_prediction_success_earns_interval_and_failure_interrupts(self):
        e=Anticipation();s=state();d=proposal();e.begin(d,s,WORLD);e.expect_action(d,s)
        after=state('next',x=s['minimap']['player'][0]+s['minimap']['cell_size'])
        r=e.feedback(s,after,receipt(),OCR,OCR,{});self.assertEqual(r['status'],'matched');self.assertTrue(e.ready(after,WORLD))
        nxt,planned=e.next();self.assertTrue(planned);self.assertEqual(nxt.keys,['d']);e.expect_action(nxt,after)
        r=e.feedback(after,after,receipt(),OCR,OCR,{});self.assertEqual(r['status'],'mismatch');self.assertFalse(e.active)
    def test_natural_change_unknown_or_unsent_is_not_success(self):
        for natural,row in [({'position':1},receipt()),({}, {'executed':False})]:
            e=Anticipation();s=state();d=proposal();e.begin(d,s,WORLD);e.expect_action(d,s)
            a=state('next',x=s['minimap']['player'][0]+s['minimap']['cell_size'])
            self.assertNotEqual(e.feedback(s,a,row,OCR,OCR,natural)['status'],'matched');self.assertFalse(e.active)
    def test_unexpected_events_cancel_queue(self):
        for event in ('scene','objects','text','damage','floor','enemy','missing'):
            e=Anticipation();s=state();e.begin(proposal(),s,WORLD);w=copy.deepcopy(WORLD);a=copy.deepcopy(s)
            if event=='scene':w['scene_change']=True
            if event=='objects':w['new_objects']=['object_000009']
            if event=='text':w['new_messages']=['新しい表示']
            if event=='damage':a['metrics']['hp']['values'][0]-=1
            if event=='floor':a['metrics']['floor']['values']=[2]
            if event=='enemy':a['minimap']['enemies']=[(1,0)]
            if event=='missing':a['eligible']=False
            self.assertFalse(e.ready(a,w),event);self.assertFalse(e.active)
    def test_empirical_reliability_changes_analysis_budget_and_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Settings();memory=MemoryStore(Path(tmp)/'m.sqlite',cfg)
            try:
                e=Anticipation(memory);s=state();cold=e.indicators(s,WORLD);key=cold['context']
                memory.db.execute('INSERT INTO forecast_stats VALUES(?,?,20,20)',(memory.profile,key));memory.db.commit()
                learned=Anticipation(memory).indicators(s,WORLD);self.assertGreater(learned['max_turns'],cold['max_turns'])
                self.assertLessEqual(learned['max_turns'],8)
                danger=copy.deepcopy(s);danger['minimap']['enemies']=[(0,1)]
                self.assertEqual(e.indicators(danger,WORLD)['max_turns'],1)
            finally:memory.close()
    def test_controller_observes_each_turn_and_executes_one_predicted_action_at_a_time(self):
        class ReceiptBackend(Backend):
            def act(self,keys,ms,stop):super().act(keys,ms,stop);return {'os_submission':'accepted','events':[{'message':'WM_KEYDOWN'}]}
        backend=ReceiptBackend();backend.observe_turn=Mock(side_effect=lambda *args:SimpleNamespace(image=b'coordinate-contract',kind='normal',changed=True,raw_change=0 if args[1]=='neutral_observation' else .1,masked_activity=0,duration_ms=1))
        d=change(scene='gameplay',action={'keys':['d'],'hold_ms':80,'wait_ms':0},forecast={'horizon':3,'first_expected':{'player_delta':[1,0]},'continuations':[{'keys':['d'],'hold_ms':80,'wait_ms':0,'expected':{'player_delta':[1,0]}}]*2})
        script=Script([d,d,d]);script.ocr=SimpleNamespace(read=Mock(return_value=OCR));script.take_state=lambda:{'scene_observation':{'status':'model_observation_unverified'}}
        with tempfile.TemporaryDirectory() as tmp:
            memory=MemoryStore(Path(tmp)/'m.sqlite',Settings())
            try:
                agent=Controller(Settings(max_steps=4),backend,script,lambda _:None,Path(tmp)/'run.jsonl',memory=memory)
                count=[0]
                def observe(*args):
                    index=count[0];count[0]+=1
                    # Each cycle: before, neutral/current, after. Supply measured
                    # coordinates directly to verify scheduling, not game behavior.
                    step=index//3+(1 if index%3==2 else 0)
                    return state(str(step),x=260+6*step)
                agent.policy.observe=Mock(side_effect=observe)
                agent.policy.choose=Mock(side_effect=lambda d,*args:(d,{'source':'model_or_neutral'}));agent.combat=None;agent.novelty=None;agent.improvement=None
                agent.world.visual.read=Mock(return_value={'status':'observed','scene_change':False})
                from autoplayer.information_exploration import InformationExploration
                with patch.object(InformationExploration,'choose',autospec=True,side_effect=lambda obj,d,n,s,b:(d,obj.context(d,n,s))):agent.run()
                rows=[json.loads(l) for l in agent.journal.read_text().splitlines() if json.loads(l)['event']=='result']
                self.assertEqual(len(backend.actions),4);self.assertEqual(len(rows),4)
                self.assertEqual([r['performance']['llm_requested'] for r in rows],[True,False,False,True])
                self.assertTrue(rows[1]['anticipation']['planned_continuation']);self.assertTrue(all(r['forecast_evaluation']['status']=='matched' for r in rows))
                self.assertGreaterEqual(script.ocr.read.call_count,12)
            finally:memory.close()
