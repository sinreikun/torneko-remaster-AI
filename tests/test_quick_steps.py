"""Single-step handoff/ROI/timing contracts, not a demo game or throughput claim."""
import copy,hashlib,io,json,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from PIL import Image
from autoplayer.quick_steps import QuickSteps
from autoplayer.ocr import ScreenOCR
from autoplayer.pacing import AdaptivePacing
from autoplayer.core import Decision

class QuickStepTests(unittest.TestCase):
    def setUp(self):
        folder=Path(__file__).parent/'fixtures';self.png=(folder/'quick-quiet-frame.png').read_bytes()
        self.state={'eligible':True,'metrics':{'hp':{'values':[20,25]},'fullness':{'values':[41]},'floor':{'values':[3]}},'minimap':{'status':'observed','player_cell':[3,4],'enemies':[]}}
        self.before=copy.deepcopy(self.state);self.before['minimap']['player_cell']=[2,4]
        self.native={'status':'ok','frame_sha256':hashlib.sha256(self.png).hexdigest(),'lines':[{'text':'HP20/25','recognition_at':1,'words':[{'box':[125,15,72,15]}]}]}
        self.row={'executed':True,'observed_keys':['d'],'input_receipt':{'os_submission':'accepted'},'policy_state':self.before}
        self.steps=QuickSteps();self.observation=SimpleNamespace(kind='normal')
    def remember(self):self.steps.remember(self.png,self.native,self.state,self.row,(0,0),self.observation,at=10.)
    def test_handoff_is_one_use_measured_map_not_input_prediction(self):
        self.remember();self.assertIsNotNone(self.steps.saved)
        value=self.steps.take(self.png,(0,0),now=10.2)
        self.assertIsNotNone(value);self.assertEqual(value['map']['player_cell'],[3,4]);self.assertFalse(value['evidence']['coordinates_predicted'])
        self.assertEqual(value['native']['lines'][0]['recognition_at'],1)
        self.assertEqual(value['neutral'].raw_change,0);self.assertIsNone(self.steps.take(self.png,(0,0),now=10.3))
    def test_no_motion_no_execution_or_enemy_cannot_seed_handoff(self):
        for kind in ('no_motion','not_executed','enemy','missing_hp','body_text'):
            with self.subTest(kind=kind):
                before=copy.deepcopy(self.before);row=copy.deepcopy(self.row);state=copy.deepcopy(self.state);native=copy.deepcopy(self.native)
                if kind=='no_motion':row['policy_state']=state
                if kind=='not_executed':row['executed']=False
                if kind=='enemy':state['minimap']['enemies']=[[1,0]]
                if kind=='missing_hp':state['metrics'].pop('hp')
                if kind=='body_text':native['lines'].append({'text':'眠った','words':[{'box':[20,200,40,15]}]})
                self.steps.remember(self.png,native,state,row,(0,0),self.observation,at=10.)
                self.assertIsNone(self.steps.saved)
    def test_expired_or_control_changed_baseline_is_rejected(self):
        self.remember();self.assertIsNone(self.steps.take(self.png,(0,0),now=12.1))
        self.remember();self.assertIsNone(self.steps.take(self.png,(1,0),now=10.1))
    def test_hud_change_or_new_dialogue_rejects_handoff(self):
        self.remember()
        with Image.open(io.BytesIO(self.png)) as im:
            im=im.convert('RGB');im.putpixel((130,20),(255,0,0));out=io.BytesIO();im.save(out,format='PNG')
        self.assertIsNone(self.steps.take(out.getvalue(),(0,0),now=10.1))
        self.remember()
        with patch('autoplayer.exterior_screen.white_frames',return_value={'visible':True}):self.assertIsNone(self.steps.take(self.png,(0,0),now=10.1))
    def test_regional_ocr_remaps_message_coordinates_and_links_full_source(self):
        ocr=ScreenOCR();result={'status':'ok','lines':[{'text':'3階','words':[{'box':[120,10,30,12]}]},{'text':'HP20/25','words':[{'box':[2,10,70,12]}]},{'text':'メッセージ','words':[{'box':[3,67,90,12]}]}]}
        ocr.read=Mock(return_value=result)
        with patch('autoplayer.exterior_screen.white_frames',return_value={'visible':False}):value=ocr.read_regions(self.png)
        self.assertEqual(value['frame_sha256'],hashlib.sha256(self.png).hexdigest());self.assertEqual(value['lines'][2]['words'][0]['box'][1],166)
        self.assertEqual(value['ocr_scope'],'hud_and_messages');self.assertFalse(value['full_screen_text_read'])
        self.assertEqual(result['lines'][2]['words'][0]['box'][1],67)
    def test_regions_are_not_used_when_frame_detection_is_unknown_or_visible(self):
        ocr=ScreenOCR();ocr.read=Mock(return_value={'status':'ok'})
        for visible in (None,True):
            with patch('autoplayer.exterior_screen.white_frames',return_value={'visible':visible}):ocr.read_regions(self.png)
            ocr.read.assert_called_with(self.png)
    def test_quick_pacing_has_separate_profile_and_never_changes_standard_margin(self):
        pace=AdaptivePacing(5000);s=copy.deepcopy(self.state);s['objective']='explore'
        d=Decision('gameplay','','','',.8,'continue',['d'],80,600)
        trial=pace.apply(d,s,{'source':'floor_navigation'},True,quick=True)
        self.assertEqual(d.wait_ms,300);self.assertEqual(trial['margin_ms'],90)
        row={'executed':True,'input_receipt':{'os_submission':'accepted'},'policy_state':s,'policy_after':s,'policy_learning':{'status':'trained','net_signals':{'position':1}}}
        for _ in range(9):pace.record(trial,row,SimpleNamespace(kind='normal',last_activity_ms=0))
        self.assertGreaterEqual(pace.wait,120);self.assertLess(pace.wait,300)
        trial=pace.apply(d,s,{'source':'floor_navigation'},True)
        self.assertEqual(d.wait_ms,600);self.assertEqual(trial['margin_ms'],250)
    def test_vector_and_scalar_map_recognizers_agree_on_real_frame(self):
        from autoplayer.map_sensor import _read_grid
        png=(Path(__file__).parent/'fixtures/battle-map-1793138526674534800.png').read_bytes()
        vector=_read_grid(png)
        with patch.dict('sys.modules',{'numpy':None}):scalar=_read_grid(png)
        self.assertEqual(vector,scalar)
