"""Real submitted orange menu pixels and nonperiodic stagnation contracts."""
import copy,hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from autoplayer.core import Settings
from autoplayer.nextgen.frame_sensor import interface_frames
from autoplayer.exterior_screen import white_frames
from autoplayer.nextgen.state import StateBuilder
from autoplayer.nextgen.store import ExperienceStore
from autoplayer.nextgen.model import ForwardModel,action_id
from autoplayer.nextgen.supervisor import guidance_quality
from test_nextgen import measured,plan

class ColoredInterfaceTests(unittest.TestCase):
    def test_real_orange_frame_is_interface_and_same_region_without_frame_is_not(self):
        root=Path(__file__).parent/'fixtures'
        positive=(root/'colored-menu-1793151109362765000.png').read_bytes()
        negative=(root/'no-menu-1793151109362765000.png').read_bytes()
        self.assertFalse(white_frames(positive)['visible'])
        self.assertTrue(interface_frames(positive)['visible']);self.assertFalse(interface_frames(negative)['visible'])
    def test_colored_frame_forces_full_ocr_before_regional_read_and_preserves_pixels(self):
        png=(Path(__file__).parent/'fixtures/colored-menu-1793151109362765000.png').read_bytes()
        ocr=Mock();ocr.read.return_value={'status':'ok','frame_sha256':hashlib.sha256(png).hexdigest(),'lines':[{'text':'道具'},{'text':'マップ'}]}
        state=StateBuilder(Settings(),ocr).build(png,False)
        self.assertTrue(state['interface_hypothesis']);ocr.read.assert_called_once_with(png);ocr.read_regions.assert_not_called()
    def test_changing_keys_cannot_hide_sixteen_steps_of_stagnation(self):
        cfg=Settings(window_title='generic',allowed_keys=list('abcdefghijklmnop'))
        with tempfile.TemporaryDirectory() as folder:
            store=ExperienceStore(Path(folder)/'w.sqlite','p','s');model=ForwardModel(store);s=measured();event=None
            try:
                for step,key in enumerate(cfg.allowed_keys,1):
                    action={'id':action_id([key],80),'keys':[key],'hold_ms':80}
                    result=model.observe(s,action,s,model.predict(s,action['id']),{'os_submission':'accepted'},step)
                    if result['loop']:event=result['loop']
                self.assertIsNotNone(event);self.assertEqual(event['reason'],'unproductive_recurrence_with_varying_inputs')
                self.assertEqual(len(event['sequence']),16)
            finally:store.close()
    def test_zero_value_key_descriptions_do_not_count_as_effective_guidance(self):
        candidates=[{'id':'A','keys':['a'],'hold_ms':80},{'id':'B','keys':['b'],'hold_ms':80}]
        p=plan();p['candidate_values']=[{'id':'A','value':0,'reason':'キーの名前'},{'id':'B','value':0,'reason':'キーの名前'}]
        self.assertFalse(guidance_quality(p,candidates)['effective'])
        p['candidate_values'][0]['value']=.5
        self.assertTrue(guidance_quality(p,candidates)['effective'])
    def test_menu_escape_does_not_unlock_unrequested_modifier_chords(self):
        cfg=Settings(window_title='generic',allowed_keys=['a','b','c'],key_bindings={'a':'上移動','b':'左移動','c':'方向転換'})
        with tempfile.TemporaryDirectory() as folder:
            store=ExperienceStore(Path(folder)/'w.sqlite','p','s');model=ForwardModel(store);s=measured();s['interface_hypothesis']=True
            model.escape={'actions':[],'nodes':[s['id']]}
            try:
                _,ranking,_=model.choose(s,model.candidates(cfg))
                self.assertTrue(all(len(r['candidate']['keys'])==1 for r in ranking))
            finally:store.close()
