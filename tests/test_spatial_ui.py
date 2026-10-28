"""Image-processing contracts using UI primitives, NOT a game simulator."""
import copy,hashlib,io,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
from PIL import Image,ImageDraw
from autoplayer.nextgen.ui_vision import UIVision,text_elements,grid_hypothesis,relationships,first_ui_action
from autoplayer.nextgen.capabilities import CapabilityManager
from autoplayer.nextgen.store import ExperienceStore
from autoplayer.nextgen.meta import MetaController
from autoplayer.nextgen.model import value_at
from autoplayer.ocr import read_spatial_regions


def sample(selected=0,style='fill',offset=(0,0),grid=False,text=True):
    image=Image.new('RGB',(320,240),'#202020');d=ImageDraw.Draw(image);lines=[]
    for i in range(6 if grid else 3):
        x=100+(i%3)*60+offset[0] if grid else 100+offset[0];y=40+(i//3)*60+offset[1] if grid else 40+i*45+offset[1]
        if selected==i:
            if style=='fill':d.rectangle((x-5,y-4,x+42,y+20),fill='#bb7700')
            elif style=='outline':d.rectangle((x-5,y-4,x+42,y+20),outline='#22ddaa',width=3)
            elif style=='underline':d.line((x,y+20,x+35,y+20),fill='white',width=3)
            elif style=='arrow':d.polygon([(x-20,y),(x-10,y+8),(x-20,y+16)],fill='white')
        if text:
            word='K'+str(i);d.text((x,y),word,fill='white');lines.append({'text':word,'confidence':.9,'words':[{'text':word,'box':[x,y,35,16]}]})
    out=io.BytesIO();image.save(out,format='PNG');png=out.getvalue();sha=hashlib.sha256(png).hexdigest()
    return png,{'status':'ok','frame_sha256':sha,'lines':lines,'captured_at':1.}

def measured(v,selected,step=0,**kwargs):
    png,ocr=sample(selected,**kwargs);ui=v.measure(png,ocr)
    return {'id':'node','context':'ui','quality':.9,'measurement_status':'ok','frame_sha256':ocr['frame_sha256'],
            'interface_hypothesis':True,'interface_observation':{'visible':True},'interface_layout':ui['layout'],'selection_observation':copy.deepcopy(ui['selection']),'ui_visual':ui,'quotes':[x['text'] for x in ocr['lines']],'metrics':{},'retained':{},'minimap':{'status':'unavailable'}},png

class SpatialUIContracts(unittest.TestCase):
    action={'id':'[["s"],80]','keys':['s'],'hold_ms':80}
    def train(self,style='fill',n=6):
        v=UIVision();a,_=measured(v,0,style=style)
        events=[]
        for i in range(1,n+1):
            b,_=measured(v,i%3,style=style);events.append(v.learn(a,b,self.action,{'os_submission':'accepted'},i));b['selection_observation']=copy.deepcopy(b['ui_visual']['selection']);a=b
        return v,a,events
    def test_positions_provenance_and_same_text_at_different_locations(self):
        png,native=sample();native['lines'][1]['words'][0]['text']='K0';native['lines'][1]['text']='K0'
        texts,missing=text_elements(native,(320,240),native['frame_sha256'])
        self.assertEqual(len(texts),3);self.assertNotEqual(texts[0]['id'],texts[1]['id']);self.assertEqual(texts[0]['frame_sha256'],native['frame_sha256']);self.assertEqual(texts[0]['confidence'],.9);self.assertFalse(missing)
    def test_single_frame_does_not_establish_selection(self):
        v=UIVision();a,_=measured(v,0)
        self.assertEqual(a['ui_visual']['selection']['status'],'unknown');self.assertIsNone(value_at(a,'selection_text'))
    def test_input_correlated_tracking_becomes_conditional_selection(self):
        v,a,events=self.train();self.assertEqual(a['ui_visual']['selection']['status'],'supported_association')
        self.assertEqual(a['ui_visual']['selection']['quote'],'K0');self.assertTrue(all(e.get('from_element')!=e.get('to_element') for e in events));self.assertTrue(a['ui_visual']['graph'])
    def test_new_marker_design_uses_movement_not_a_fixed_template(self):
        for style in ('fill','outline','arrow','underline'):
            with self.subTest(style=style):
                v,a,events=self.train(style)
                self.assertTrue(any(e.get('to_element') for e in events),style)
                self.assertGreater(max(e.get('confidence',0) for e in events),.7,style)
    def test_grid_structure_is_measured_after_translation(self):
        for offset in ((0,0),(8,15)):
            png,native=sample(grid=True,offset=offset);texts,_=text_elements(native,(320,240),native['frame_sha256']);grid=grid_hypothesis(texts)
            self.assertEqual((grid['rows'],grid['columns']),(2,3));self.assertEqual(len(grid['cells']),6)
    def test_natural_changes_and_rejected_input_do_not_increase_support(self):
        v=UIVision();a,_=measured(v,0);b,_=measured(v,1)
        r=v.learn(a,b,self.action,{'os_submission':'accepted'},1,{'ui_local_change':1});self.assertEqual(r['status'],'natural_change_confounded')
        v.learn(a,b,self.action,{'os_submission':'failed'},2);self.assertFalse(v.models[b['ui_visual']['layout']]['relations'])
    def test_ambiguous_relationships_remain_hypotheses(self):
        texts=[{'id':str(i),'text':'same','bbox':[.3,.3,.5,.4],'center':[.4,.35]} for i in range(2)]
        rs=relationships([.28,.28,.52,.42],texts);self.assertEqual(len(rs),2);self.assertEqual(rs[0]['cost'],rs[1]['cost'])
    def test_incorrect_previous_mapping_is_counterexample(self):
        v,a,_=self.train(n=4);a['ui_visual']['selection']['element_id']='wrong'
        b,_=measured(v,2);r=v.learn(a,b,self.action,{'os_submission':'accepted'},50)
        h=v.models[b['ui_visual']['layout']]['relations']['contains'];self.assertGreater(h['counterexamples'],0);self.assertEqual(h['status'],'under_review')
    def test_ui_graph_can_choose_first_input_and_never_a_macro(self):
        v,a,_=self.train();self.assertEqual(first_ui_action(a['ui_visual'],'K1'),self.action['id']);self.assertIsNone(first_ui_action(a['ui_visual'],'absent'))
    def test_unknown_selection_requests_observer_adaptation(self):
        v=UIVision();a,_=measured(v,0)
        with tempfile.TemporaryDirectory() as folder:
            store=ExperienceStore(Path(folder)/'w.sqlite','p','s')
            try:
                m=MetaController(store,'ゲームを進める')
                for i in range(4):m.observer.observe(a,{'match':None},100,False,i,0.)
                m.prepare(a,4);self.assertIn('ui_observation_missing',m.failures);self.assertEqual(m.observer.trial['item'],'ui_region_ocr')
                self.assertEqual(m.recovery['failure'],'ui_observation_missing')
            finally:store.close()
    def test_capabilities_use_position_supported_selection(self):
        v,a,_=self.train(n=4)
        with tempfile.TemporaryDirectory() as folder:
            store=ExperienceStore(Path(folder)/'w.sqlite','p','s')
            try:
                c=CapabilityManager(store)
                for i in (5,6):
                    b,_=measured(v,i%3);v.learn(a,b,self.action,{'os_submission':'accepted'},i);b['selection_observation']=copy.deepcopy(b['ui_visual']['selection'])
                    c.observe(a,self.action,b,{'status':'updated'},i);a=b
                self.assertTrue(any(s['name']=='change_selection' and s['successes'] for s in c.skills.values()))
            finally:store.close()
    def test_region_ocr_restores_coordinates_and_source_frame(self):
        png,native=sample();reader=Mock()
        def read(crop):return {'status':'ok','frame_sha256':hashlib.sha256(crop).hexdigest(),'lines':[{'text':'x','words':[{'text':'x','box':[2,3,10,10]}]}]}
        reader.read.side_effect=read;result=read_spatial_regions(reader,png,[[.25,.25,.75,.75]])
        self.assertEqual(result['lines'][0]['words'][0]['box'][:2],[82,63]);self.assertEqual(result['frame_sha256'],hashlib.sha256(png).hexdigest())
    def test_models_persist_but_current_selection_does_not(self):
        v,a,_=self.train();other=UIVision();other.restore(list(v.models.values()))
        self.assertIsNone(other.last);self.assertTrue(other.models)
        before,_=measured(other,0);after,_=measured(other,1)
        event=other.learn(before,after,self.action,{'os_submission':'accepted'},1)
        self.assertNotEqual(event['status'],'duplicate')
    def test_counterexample_revokes_current_selection_until_revalidated(self):
        v,a,_=self.train(n=4);a['ui_visual']['selection']['element_id']='wrong'
        b,_=measured(v,2);v.learn(a,b,self.action,{'os_submission':'accepted'},70)
        self.assertEqual(b['ui_visual']['selection']['status'],'unknown')
        self.assertIn('selection_text_correspondence_unknown',b['ui_visual']['needs_observation'])
    def test_unreadable_cells_have_geometry_and_movement_without_invented_text(self):
        v=UIVision();a,_=measured(v,0,text=False)
        for i in range(1,7):
            b,_=measured(v,i%3,text=False);v.learn(a,b,self.action,{'os_submission':'accepted'},i);a=b
        self.assertTrue(a['ui_visual']['cells']);self.assertTrue(a['ui_visual']['graph'])
        self.assertTrue(all(c['text'] is None for c in a['ui_visual']['cells']))
    def test_current_visualization_uses_matching_observation_frame(self):
        from autoplayer.nextgen.ui_vision import paint_ui
        v,a,_=self.train();canvas=Mock();paint_ui(canvas,a['ui_visual'],(0,0,640,480))
        self.assertTrue(canvas.create_rectangle.called)
    def test_new_region_measurement_re_evaluates_missing_geometry(self):
        v=UIVision();png,native=sample();unknown=copy.deepcopy(native)
        for line in unknown['lines']:line['words']=[]
        old=v.measure(png,unknown);new=v.measure(png,native,{'ui_region_ocr':1})
        self.assertTrue(old['unlocated_text']);self.assertFalse(new['unlocated_text']);self.assertEqual(len(new['elements']),3)
    def test_invalid_progress_does_not_erase_valid_local_evidence_and_reports_repair(self):
        from autoplayer.nextgen.brief_protocol import decode
        from autoplayer.nextgen.supervisor import validate
        from test_autonomy import node,state
        reply={'local_objective':'閉じる効果を検証','reason':'現在の可視条件を測る','preferred':[],'discouraged':[],'success_test':'interface_closed','success_text':'','review_after':16,'progress_nodes':[node('self',['self'])]}
        plan,issues=decode(reply,{},[],state(menu=True),validate)
        self.assertTrue(issues);self.assertNotIn('progress_nodes',plan);self.assertEqual(plan['targets'][0]['feature'],'interface_open')
    def test_progress_schema_provides_new_ids_and_explicit_repair_feedback(self):
        from autoplayer.nextgen.brief_protocol import schema_for
        schema=schema_for({},True,['new0','new1'],['old'])
        self.assertEqual(schema['properties']['progress_nodes']['items']['properties']['id']['enum'],['new0','new1'])
        with tempfile.TemporaryDirectory() as folder:
            store=ExperienceStore(Path(folder)/'w.sqlite','p','s')
            try:
                m=MetaController(store,'ゲームを進める');m.planning_feedback({},0,'自己参照')
                self.assertEqual(m.context()['planning_issue'],'自己参照');self.assertTrue(m.needs_plan(20))
            finally:store.close()
    def test_prompt_only_ability_label_is_not_activated_as_a_game_text_goal(self):
        from test_autonomy import node,state
        with tempfile.TemporaryDirectory() as folder:
            store=ExperienceStore(Path(folder)/'w.sqlite','p','s')
            try:
                m=MetaController(store,'ゲームを進める')
                proposal=node('future',conditions=[{'feature':'text','operator':'contains','value':'inspect'}])
                m.accept({'objective':'観測','targets':[],'progress_nodes':[proposal]},state(),0)
                self.assertIsNone(m.progress.current());self.assertEqual(m.progress.nodes['future']['unverified_literals'],['inspect'])
                m.progress.update(state('real',text='Inspect'),1)
                self.assertEqual(m.progress.nodes['future']['unverified_literals'],[])
            finally:store.close()
