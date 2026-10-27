"""Contracts for submitted menu OCR and dispatch; no game is simulated."""
import copy,io,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from PIL import Image
from autoplayer.interfaces import torneko_menu_kind,main_menu_context
from autoplayer.fast_learning import FastLearningGate
from autoplayer.core import Settings,Controller,parse_decision
from autoplayer.memory import MemoryStore
from autoplayer.information_exploration import InformationExploration
import test_policy as fixtures
from test_agent import Backend,Script,change

LABELS=['道具','プレイ記録','倉庫','せってい','中断','その他','話す']
def native(extra=()):return {'status':'ok','lines':[{'text':t,'confidence':.99} for t in LABELS+list(extra)]}
def state(name,kind=None):
    s=fixtures.state(name);s.update(eligible=False,objective='interface',metrics={},text_reliable=True,interface_kind=kind,menu_evidence=bool(kind));return s

class TownMenuTests(unittest.TestCase):
    setUp=fixtures.PolicyTests.setUp
    def test_multiple_town_labels_identify_menu_without_floating_prompt(self):
        self.assertEqual(torneko_menu_kind(native()),'main_menu')
        self.assertEqual(torneko_menu_kind({'status':'ok','lines':[{'text':t} for t in ['▶道具','せってい','その他']]}),'main_menu')
        self.assertIsNone(torneko_menu_kind({'status':'ok','lines':[{'text':'話す'}]}))
    def test_main_menu_context_ignores_noise_but_keeps_extra_messages(self):
        base=main_menu_context(native());self.assertEqual(base,main_menu_context(native(['9','ツ話す'])))
        self.assertNotEqual(base,main_menu_context(native(['アイテムを持っていません。'])))
        info=InformationExploration(self.cfg);d=parse_decision(json.dumps(change()),self.cfg)
        self.assertEqual(info.situation(d,native(),{}),info.situation(d,native(['0','ツ話す']),{}))
        output=io.BytesIO();Image.new('RGB',(64,64),(50,100,150)).save(output,format='PNG')
        self.assertEqual(self.policy.observe(output.getvalue(),native())['text_key'],self.policy.observe(output.getvalue(),native(['0']))['text_key'])
    def test_known_interfaces_need_current_decisions_and_short_prompt_needs_image(self):
        gate=FastLearningGate(128);s={'visual':[0]*192}
        for turn in range(3):
            result=gate.evaluate(turn,native(),s,{})
            self.assertTrue(result['llm_requested']);self.assertTrue(result['interface_decision']);self.assertEqual(result['next_review_in'],0)
        prompt={'status':'ok','lines':[{'text':'話す'}]}
        self.assertTrue(gate.evaluate(3,prompt,s,{})['visual_fallback'])
        duplicated={'status':'ok','lines':prompt['lines']*4}
        self.assertTrue(gate.evaluate(3,duplicated,s,{})['visual_fallback'])
        ready={**s,'eligible':True,'minimap':{'status':'observed'},'metrics':{'hp':{'values':[15,15]}}}
        self.assertFalse(gate.evaluate(4,{'status':'ok','lines':[{'text':'HP15/15'}]},ready,{})['llm_requested'])
    def test_opening_and_closing_menu_do_not_get_positive_text_gain(self):
        a,b=state('A'),state('B','main_menu')
        for before,after,key in [(a,b,'num0'),(b,a,'num2')]:
            event=self.policy.learn(before,after,fixtures.receipt_row(key),before)
            self.assertEqual(event['interface_information_gain'],0);self.assertLessEqual(event['reward'],0)
    def test_fresh_menu_decisions_survive_novelty_and_coverage_layers(self):
        cfg=Settings(max_steps=2,fast_learning=True);script=Script([change(action={'keys':[k],'hold_ms':80,'wait_ms':0}) for k in ('num2','num1')])
        script.ocr=SimpleNamespace(read=Mock(return_value=native()))
        class Receipt(Backend):
            def act(self,*args):super().act(*args);return {'os_submission':'accepted','events':[{'message':'WM_KEYDOWN'}]}
        backend=Receipt();backend.observe_turn=Mock(return_value=SimpleNamespace(image=b'current',kind='normal',changed=False,raw_change=0,masked_activity=0,duration_ms=500))
        with tempfile.TemporaryDirectory() as t:
            memory=MemoryStore(Path(t)/'memory.sqlite',cfg)
            try:
                agent=Controller(cfg,backend,script,lambda _:None,Path(t)/'run',memory=memory)
                s=state('menu','main_menu');s['minimap']={'status':'unavailable'}
                agent.policy.observe=Mock(side_effect=lambda *args,**kw:copy.deepcopy(s))
                agent.novelty.choose_menu=Mock(side_effect=AssertionError('fresh menu input must not be replaced'))
                agent.run();rows=[json.loads(l) for l in agent.journal.read_text().splitlines() if json.loads(l).get('event')=='result']
            finally:memory.close()
        self.assertEqual(len(script.calls),2);self.assertEqual([a[0] for a in backend.actions],[['num2'],['num1']])
        self.assertTrue(all(r['interface_model_input_retained'] for r in rows))
