"""Selection/accounting and OCR contracts; no game simulation or live input."""
import copy,io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from autoplayer.core import Settings,Decision
from autoplayer.exterior_actions import ExteriorActions
from autoplayer.mapless_navigation import MaplessNavigation
from autoplayer.active_input import ActiveInput
from autoplayer.interfaces import ordinary_choice,choice_action,torneko_menu_kind
from autoplayer.learning_connection import candidate,report,summary
from autoplayer.memory import MemoryStore
from autoplayer.policy import ExperiencePolicy


def ocr(*texts):return {'status':'ok','lines':[{'text':t,'confidence':.99} for t in texts]}
def decision(keys):return Decision('menu','','','',.9,'continue',keys,80,600)

class ChoiceContracts(unittest.TestCase):
    def setUp(self):
        self.cfg=Settings();self.scope=ExteriorActions(self.cfg,MaplessNavigation(self.cfg))
    def test_retry_choice_is_not_a_menu_cancel(self):
        native=ocr('大きいパンを1つ持って このダンジョンをやりなおしますか？','「いいえ」をえらぶと地上にもどります。','いいえ','▶はい')
        context=self.scope.observe({'menu_evidence':True},native,'menu')
        self.assertEqual(context['mode'],'ordinary_choice');self.assertEqual(context['actions'],[['num1']])
        selected,_=self.scope.enforce(decision(['num2']));self.assertEqual(selected.keys,['num1'])
    def test_unread_cursor_never_reintroduces_cancel_even_on_repetition(self):
        native=ocr('これでいい？','いいえ','はい');context=self.scope.observe({'menu_evidence':True},native,'menu')
        active=ActiveInput(self.cfg)
        for _ in range(24):
            selected,_=active.select(decision(['num2']),native,{},blocked=context['actions'],scope_actions=context['actions'])
            self.assertNotIn('num2',selected.keys);active.sent(selected,native,{})
    def test_selected_no_moves_toward_yes_using_option_positions(self):
        native=ocr('▶いいえ','はい')
        for line,x in zip(native['lines'],(20,100)):line['words']=[{'box':[x,20,x+20,40]}]
        context=self.scope.observe({'menu_evidence':True},native,'menu')
        self.assertEqual(context['actions'],[['d']]);self.assertEqual(self.scope.enforce(decision(['num1']))[0].keys,['d'])
    def test_prose_yes_is_dialogue_not_choice_or_menu(self):
        native=ocr('ネネ「あら もうでかけるの？ はい おべんとう。')
        self.assertFalse(ordinary_choice(native))
        context=self.scope.observe({'menu_evidence':True,'white_frame_evidence':{'visible':True}},native,'menu')
        self.assertEqual(context['mode'],'advance_existing_dialog');self.assertEqual(context['actions'],[['num1']])
    def test_actual_inventory_closes_but_following_choice_confirms(self):
        native=ocr('持ち出しアイテム','道具(0/3)','詳細表示切替','整理','反映')
        self.assertEqual(torneko_menu_kind(native),'carry_inventory')
        self.assertEqual(self.scope.observe({'menu_evidence':True},native,'menu')['actions'],[['num2']])
        self.assertNotIn(['num2'],self.scope.observe({'menu_evidence':True},ocr('これでいい？','はい','いいえ'),'menu')['actions'])
    def test_destructive_choice_still_cancels(self):
        native=ocr('セーブデータを削除しますか？','▶はい','いいえ')
        self.assertEqual(self.scope.observe({},native,'dialog')['actions'],[['num2']])
        self.assertEqual(ActiveInput(self.cfg).select(decision(['num1']),native)[0].keys,['num2'])

class LearningConnectionContracts(unittest.TestCase):
    def row(self):return {'executed':True,'observed_keys':['w'],'learning_candidate':candidate({'source':'learned_policy','selected':{'trials':4,'q':.3}},decision(['w'])),
        'policy_learning':{'status':'trained','reward':.1},'experience_review':{'status':'recorded'}}
    def test_record_update_reuse_are_independent(self):
        row=self.row();value=report(row,{},{});self.assertTrue(value['policy_candidate_retained']);self.assertTrue(value['retained_candidate_has_experience'])
        self.assertTrue(value['experience_recorded']);self.assertEqual(value['outcome_update']['status'],'trained')
        row['active_input']={'source':'information_collection'};value=report(row,{},{})
        self.assertTrue(value['candidate_keys_equal']);self.assertFalse(value['policy_candidate_retained']);self.assertIn('上書き',summary(value))
    def test_zero_trial_candidate_is_not_experience_reuse(self):
        row=self.row();row['learning_candidate']['prior_trials']=0
        value=report(row,{},{});self.assertTrue(value['policy_candidate_retained']);self.assertFalse(value['retained_candidate_has_experience'])
    def test_nonexecuted_censored_and_no_review_do_not_claim_learning(self):
        row=self.row();row.update(executed=False,policy_learning={'status':'censored'},experience_review={'status':'excluded'})
        value=report(row,{},{});self.assertFalse(value['policy_candidate_retained']);self.assertFalse(value['experience_recorded']);self.assertFalse(value['outcome_update']['goal_success_verified'])
    def test_map_modes_and_scope_remain_distinct(self):
        before={'eligible':True,'minimap':{'status':'observed'},'observation_mode':'num7_held'};after=copy.deepcopy(before)
        self.assertEqual(report(self.row(),before,after)['outcome_update']['scope'],'spatial')
        after['observation_mode']='normal'
        self.assertFalse(report(self.row(),before,after)['outcome_update']['map_pair_observed'])
    def test_fast_map_reference_is_observable_without_second_ocr(self):
        with tempfile.TemporaryDirectory() as t:
            cfg=Settings(fast_learning=True);memory=MemoryStore(Path(t)/'m.sqlite',cfg)
            try:
                policy=ExperiencePolicy(memory,cfg);raw=io.BytesIO();Image.new('RGB',(32,24),(20,40,60)).save(raw,format='PNG');png=raw.getvalue()
                normal=policy.observe(png,ocr('1階 HP15/15'))
                grid={'status':'observed','sensor':'fixed_grid_54x32','cell_size':12,'player_cell':[3,3],'known_cells':[[0,0]],'enemies':[],'items':[],'stairs':[]}
                with patch('autoplayer.policy.read_grid',return_value=grid):observed=policy.observe(png,None,map_display=True,reference=normal)
                self.assertTrue(observed['eligible']);self.assertTrue(observed['observable'])
                self.assertEqual(observed['metrics_source'],'normal_capture_before_map_display')
                self.assertFalse(policy.observe(png,None)['observable'])
            finally:memory.close()

    def test_controller_requests_same_input_map_pair_and_logs_connection(self):
        from autoplayer.core import Controller
        from autoplayer.map_sampling import MapSamplingPlan
        from test_agent import Backend,Script,change
        from test_policy import state
        with tempfile.TemporaryDirectory() as t:
            class ReceiptBackend(Backend):
                def act(self,keys,ms,stop):
                    super().act(keys,ms,stop)
                    return {'os_submission':'accepted','events':[{'message':'WM_KEYDOWN'}]}
            cfg=Settings(max_steps=1,fast_learning=True);memory=MemoryStore(Path(t)/'m.sqlite',cfg)
            try:
                controller=Controller(cfg,ReceiptBackend(),Script([change()]),lambda _:None,Path(t)/'run',memory=memory)
                controller.map_plan=MapSamplingPlan();controller.map_plan.invalid=False
                held=state();held['observation_mode']='num7_held';seen=[]
                def sample(image,current,phase,baseline=False):
                    if phase=='map_result':seen.append(controller.map_plan.invalid)
                    return copy.deepcopy(held),copy.deepcopy(held),None
                with patch.object(controller,'map_sample',side_effect=sample):controller.run()
                rows=[json.loads(line) for line in controller.journal.read_text().splitlines()]
                row=next(r for r in rows if r.get('event')=='result')
                self.assertEqual(seen,[True]);self.assertTrue(row['map_pair_for_learning']['observed'])
                self.assertEqual(row['learning_connection']['outcome_update']['scope'],'spatial')
                self.assertEqual(row['policy_learning']['status'],'trained',str({k:row.get(k) for k in ('policy_learning','active_input','policy_choice','learning','action_kind','input_receipt','observed_keys')}))
                self.assertTrue(row['learning_connection']['experience_recorded'])
            finally:memory.close()
