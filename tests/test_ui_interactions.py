"""Empirical UI response contracts. No game environment is simulated."""
import copy,tempfile,unittest
from pathlib import Path
from test_spatial_ui import measured
from autoplayer.nextgen.ui_vision import UIVision
from autoplayer.nextgen.ui_interactions import UIInteractionModel,family_of
from autoplayer.nextgen.capabilities import CapabilityManager,scope
from autoplayer.nextgen.meta import MetaController
from autoplayer.nextgen.observer_adaptation import ObserverAdaptation,DEFAULTS
from autoplayer.nextgen.store import ExperienceStore
from autoplayer.nextgen.progress import ProgressManager
from test_autonomy import node,condition


def panel(text,index):
    return {'id':'node'+str(index),'context':'word-layout:'+str(index),'frame_sha256':'frame'+str(index),'quality':.8,'quotes':[text,text],
            'measurement_status':'ok','interface_hypothesis':True,'interface_layout':'layout'+str(index),'interface_observation':{'visible':True,'rectangles':[[.15,.65,.85,.94]]},
            'selection_observation':{'status':'unknown','element_id':None,'quote':None},'metrics':{},'retained':{},'minimap':{'status':'unavailable'},
            'ui_visual':{'active':True,'layout':'layout'+str(index),'elements':[{'id':'t','text':text,'bbox':[.2,.7,.6,.75]}],'selection':{'status':'unknown','element_id':None,'quote':None},
                         'grid':{'status':'unknown'},'candidates':[],'needs_observation':['selection_text_correspondence_unknown'],'coverage':1/3}}

class UIInteractionContracts(unittest.TestCase):
    confirm={'id':'confirm','keys':['q'],'hold_ms':80,'declared_meanings':['confirm']}
    direction={'id':'direction','keys':['x'],'hold_ms':80,'declared_meanings':['up']}
    cancel={'id':'cancel','keys':['z'],'hold_ms':80,'declared_meanings':['cancel']}
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=ExperienceStore(Path(self.tmp.name)/'w.sqlite','profile','run')
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def train(self,model=None,n=4,neutral=True):
        model=model or UIInteractionModel();before=panel('page0',0);model.attach(before)
        for i in range(1,n+1):
            after=panel('page'+str(i),i);model.attach(after)
            e=model.observe(before,self.confirm,after,{'status':'updated'},{'os_submission':'accepted'},i,{'status':'visual_reorganization','panel_pixel_change':10}, {'ui_text_content_changed':0} if neutral else None);before=after
        return model,before
    def test_changing_sentences_share_structure_but_not_selection_truth(self):
        model,s=self.train();flow=s['ui_visual']['interaction_model']
        self.assertEqual(family_of(panel('different text',20)),flow['family']);self.assertEqual(flow['selection_requirement'],'not_required_for_supported_text_flow')
        self.assertEqual(s['selection_observation']['status'],'unknown');self.assertNotIn('selection_text_correspondence_unknown',s['ui_visual']['needs_observation'])
    def test_measured_response_is_reused_and_direction_bonus_expires(self):
        model,s=self.train()
        for i in range(4):model.observe(s,self.direction,s,{'status':'updated'},{'os_submission':'accepted'},20+i,{'status':'no_measured_ui_change','panel_pixel_change':0})
        model.attach(s);values={v['id']:v['value'] for v in model.priors(s,[self.confirm,self.direction],True)}
        self.assertGreater(values['confirm'],0);self.assertLess(values['direction'],0)
        m=MetaController(self.store,'進める');m.prepare(s,30);plan=m.guide({'targets':[],'candidate_values':[]},s,[self.confirm,self.direction])
        scores={v['id']:v['value'] for v in plan['candidate_values']};self.assertLess(scores['direction'],scores['confirm'])
    def test_natural_text_changes_are_not_input_effects(self):
        model=UIInteractionModel();before=panel('old',0);after=panel('new',1)
        for i in range(5):model.observe(before,self.confirm,after,{'status':'updated'},{'os_submission':'accepted'},i,{'panel_pixel_change':10},{'ui_text_content_changed':1})
        model.attach(after);self.assertEqual(after['ui_visual']['interaction_model']['selection_requirement'],'unknown')
        self.assertEqual(model.model(before)['actions']['confirm']['text_responses'],0)
    def test_uncontrolled_changes_alone_do_not_establish_nonselective_mode(self):
        _,state=self.train(neutral=False);self.assertEqual(state['ui_visual']['interaction_model']['selection_requirement'],'unknown')
    def test_ocr_changes_without_pixel_evidence_do_not_establish_flow(self):
        model=UIInteractionModel()
        for i in range(4):model.observe(panel('a',0),self.confirm,panel('b',i+1),{'status':'updated'},{'os_submission':'accepted'},i,{'panel_pixel_change':0},{'ui_text_content_changed':0})
        self.assertEqual(model.model(panel('a',0))['actions']['confirm']['text_responses'],0)
    def test_selection_cues_do_not_become_cursor_absence(self):
        model,s=self.train();s['ui_visual']['candidates']=[{'status':'symbol_hypothesis'}];model.attach(s)
        self.assertEqual(s['ui_visual']['interaction_model']['selection_requirement'],'selection_capable')
    def test_capability_is_reusable_across_text_changes(self):
        model,s=self.train();skills=CapabilityManager(self.store)
        for i in range(6,9):
            after=panel('fresh page '+str(i),i);model.attach(after)
            model.observe(s,self.confirm,after,{'status':'updated'},{'os_submission':'accepted'},i,{'panel_pixel_change':10},{'ui_text_content_changed':0})
            skills.observe(s,self.confirm,after,{'status':'updated'},i);s=after
        self.assertTrue(skills.matching('advance_interface',s));self.assertEqual(scope(s,'advance_interface')['condition'],family_of(s))
    def test_repeated_text_cycle_switches_to_alternatives(self):
        model,s=self.train();model.model(s)['seen_text']=[]
        for i in range(10,32):
            after=panel('known '+str(i%3),i);model.attach(after)
            model.observe(s,self.confirm,after,{'status':'updated'},{'os_submission':'accepted'},i,{'panel_pixel_change':10},{'ui_text_content_changed':0});s=after
        self.assertTrue(s['ui_visual']['interaction_model']['cycle_detected'])
        values={v['id']:v['value'] for v in model.priors(s,[self.confirm,self.cancel],True)};self.assertLess(values['confirm'],0);self.assertGreater(values['cancel'],0)
    def test_observer_trial_rolls_back_on_task_change(self):
        observer=ObserverAdaptation(self.store);before=panel('same',0)
        for i in range(4):observer.observe(before,{'match':False},100,False,i,.3)
        self.assertTrue(observer.start('ui_observation_missing',4));_,after=self.train()
        event=observer.observe(after,{'match':True},90,False,5,1.)
        self.assertEqual(event['status'],'rollback');self.assertEqual(event['reason'],'observation_context_changed')
    def test_known_flow_disables_extra_region_ocr_without_discarding_base_policy(self):
        observer=ObserverAdaptation(self.store);observer.policy['ui_region_ocr']=1;_,s=self.train()
        self.assertEqual(observer.effective_policy(s)['ui_region_ocr'],0);self.assertEqual(observer.policy['ui_region_ocr'],1)
    def test_actual_contradiction_is_presented_and_equivalent_failed_goal_rejected(self):
        p=ProgressManager(self.store);proposal=node('one',conditions=[condition(value='menu')]);proposal['counterconditions']=[condition(value='stop')];p.propose([proposal])
        s=panel('menu stop',1);p.update(s,1);self.assertEqual(p.nodes['one']['status'],'disputed')
        repeat=copy.deepcopy(proposal);repeat['id']='other'
        with self.assertRaises(ValueError):p.propose([repeat])
        self.store.queue.join();m=MetaController(self.store,'進める');m.prepare(s,2);context=m.planning_context(s,2)
        self.assertTrue(context['blocked_progress'][0]['counterevidence'])
    def test_persistence_and_rejected_receipts(self):
        model,s=self.train();m=model.model(s);self.store.put('ui_interactions',m,key=m['key']);self.store.queue.join()
        restored=UIInteractionModel();restored.restore(self.store.load('ui_interactions'));new=panel('unseen',100);restored.attach(new)
        self.assertEqual(new['ui_visual']['interaction_model']['selection_requirement'],'not_required_for_supported_text_flow')
        result=restored.observe(s,self.confirm,new,{'status':'updated'},{'os_submission':'failed'},100,{'panel_pixel_change':10})
        self.assertEqual(result['status'],'censored_or_unscoped')
    def test_reusing_preobservation_requires_exact_same_source_frame(self):
        import hashlib,threading
        from types import SimpleNamespace
        from unittest.mock import Mock
        from autoplayer.nextgen.controller import NextGenController
        runner=NextGenController.__new__(NextGenController);runner.cfg=SimpleNamespace(window_title='Other game');runner.backend=SimpleNamespace();runner.cancel=threading.Event()
        source=b'source identity contract only';fresh=panel('current',3);fresh['frame_sha256']=hashlib.sha256(source).hexdigest();fresh['floor_confirmation']={'status':'unreadable','confirmed':None}
        runner.builder=SimpleNamespace(build=Mock(return_value=fresh));old={**fresh,'frame_sha256':'older-frame'}
        self.assertIs(runner.sense(source,base_state=old),fresh);runner.builder.build.assert_called_once()
        runner.builder.build.reset_mock();self.assertIs(runner.sense(source,base_state=fresh),fresh);runner.builder.build.assert_not_called()
    def test_accepted_observer_settings_can_be_rechallenged_toward_baseline(self):
        observer=ObserverAdaptation(self.store);observer.policy['cache_uses']=0;s=panel('same',0)
        for i in range(4):observer.observe(s,{'match':True},100,False,i,.8)
        self.assertTrue(observer.start('ui_observation_missing',4));self.assertEqual(observer.trial['item'],'cache_uses');self.assertEqual(observer.policy['cache_uses'],DEFAULTS['cache_uses'])
    def test_selection_measurement_improves_without_changing_the_measurement_task(self):
        observer=ObserverAdaptation(self.store);s=panel('same',0)
        for i in range(4):observer.observe(s,{'match':False},100,False,i,.3)
        observer.start('ui_observation_missing',4)
        improved=copy.deepcopy(s);improved['ui_visual']['interaction_model']={'family':None,'selection_requirement':'selection_capable'}
        event=observer.observe(improved,{'match':True},100,False,5,1.)
        self.assertIsNone(event);self.assertIsNotNone(observer.trial)
    def test_borderless_stable_ui_still_bounds_direction_experiments(self):
        model=UIInteractionModel();s=panel('same',0);s['interface_observation']['rectangles']=[];model.attach(s)
        for i in range(3):model.observe(s,self.direction,s,{'status':'updated'},{'os_submission':'accepted'},i,{'status':'no_measured_ui_change'})
        model.attach(s);values=model.priors(s,[self.direction],True)
        self.assertLess(values[0]['value'],0)
    def test_new_selection_cue_does_not_inherit_blind_text_advance(self):
        model,s=self.train();previous_family=family_of(s)
        s['ui_visual']['candidates']=[{'status':'symbol_hypothesis'}];model.attach(s)
        self.assertNotEqual(family_of(s),previous_family)
        self.assertFalse(any(v['id']=='confirm' and v['value']>0 for v in model.priors(s,[self.confirm,self.direction],True)))
