"""Replay of real text-entry OCR, bounded goal realization, not a demo game."""
import copy,json,tempfile,unittest
from pathlib import Path
from autoplayer.core import Settings
from autoplayer.nextgen.text_evidence import collect,excerpt
from autoplayer.nextgen.brief_protocol import unavailable_key_hints
from autoplayer.nextgen.model import ForwardModel,effects
from autoplayer.nextgen.store import ExperienceStore
from test_nextgen import measured,plan

class TextEntryTests(unittest.TestCase):
    def test_real_footer_is_not_lost_after_more_than_forty_keyboard_lines(self):
        native=json.loads((Path(__file__).parent/'fixtures/text-entry-1793162960451856100.json').read_text())
        self.assertGreater(len(native['lines']),40)
        quotes,footer,masked=collect(native,[1280,720])
        self.assertIn('Tab',footer);self.assertIn('おわる',footer)
        selected=excerpt({'quotes':quotes,'footer_quotes':footer})
        self.assertIn('Tab',selected);self.assertIn('おわる',selected);self.assertLessEqual(len(selected),20)
        hints=unavailable_key_hints(quotes,['num1','num2'])
        self.assertEqual(hints[0]['key'],'tab')
    def test_masked_content_change_is_not_absorbed_by_layout_normalization(self):
        a=measured();b=copy.deepcopy(a)
        a['editable_content_candidates']=['***********'];b['editable_content_candidates']=['**********']
        self.assertEqual(effects(a,b)['editable_content_changed'],1)
        b['measurement_status']='unavailable'
        self.assertIsNone(effects(a,b)['editable_content_changed'])
    def test_closing_goal_can_try_declared_cancel_role_but_not_forever_or_outside_interface(self):
        cfg=Settings(window_title='generic',allowed_keys=['a','b'],key_bindings={'a':'上移動','b':'キャンセル'})
        with tempfile.TemporaryDirectory() as folder:
            store=ExperienceStore(Path(folder)/'w.sqlite','p','s');model=ForwardModel(store);state=measured();state['interface_hypothesis']=True
            target=plan();target['targets']=[{'feature':'interface_open','operator':'eq','value':0,'weight':1}]
            try:
                candidates=model.candidates(cfg);selected,_,_=model.choose(state,candidates,target)
                self.assertEqual(selected['candidate']['keys'],['b']);self.assertTrue(selected['goal_affordance_trial'])
                for step in (1,2):
                    action=selected['candidate'];model.observe(state,action,state,model.predict(state,action['id']),{'os_submission':'accepted'},step)
                selected,ranking,_=model.choose(state,candidates,target)
                entry=next(r for r in ranking if r['candidate']['keys']==['b'] and r['candidate']['hold_ms']==80)
                self.assertFalse(entry['goal_affordance_trial']);self.assertFalse(any(r['goal_affordance_trial'] for r in ranking))
                state['interface_hypothesis']=False
                _,ranking,_=model.choose(state,candidates,target)
                self.assertFalse(any(r['goal_affordance_trial'] for r in ranking))
            finally:store.close()
