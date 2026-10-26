"""Operational-plan, concurrency, empirical effects and dispatch contracts.
No fictional game, simulated gameplay, live key input or model service.
"""
import copy,json,tempfile,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from autoplayer.core import Settings,Decision,Controller,LocalLLM
from autoplayer.memory import MemoryStore
from autoplayer.supervision import Supervisor,StateLedger,validate_plan
from autoplayer.action_effects import ActionEffects
from autoplayer.operational_control import OperationalControl
from autoplayer.exterior_actions import ExteriorActions
from autoplayer.mapless_navigation import MaplessNavigation
from autoplayer.combat import CombatExperiments
from test_policy import state,receipt_row
from test_agent import Backend,Script,change

def proposal(**kw):
    return dict(objective='現在の観測から目的への経路を進む',skill='explore',reason='実測を優先する',budget_turns=16,exploration_fraction=.125,strategy='advance',navigation='current_goal',floor_objective='auto',item_decisions=[],hypothesis='',test='none',test_attempts=2,expected_signal='position',interface_role='none',interface_quote='',**kw)
def native(*lines):return {'status':'ok','lines':[{'text':t,'confidence':.99} for t in lines]}
def ledger(s=None,step=0,revision=0,guidance=0):
    l=StateLedger();l.observe(native('1階 HP15/15'),s or state(),{'location':'dungeon'},{},{'revision':guidance,'text':''},revision,step);return l
def reply_ready(supervisor):
    value=supervisor.result.get(timeout=2);supervisor.result.put(value)

class SupervisorTests(unittest.TestCase):
    def test_plan_validation_rejects_code_keys_unbounded_and_invalid_modes(self):
        self.assertEqual(validate_plan(proposal())['skill'],'explore')
        for patch in ({'budget_turns':999},{'exploration_fraction':float('nan')},{'skill':'run_code'},{'interface_role':'alt_tab'},{'test':'movement_response'},{'test_attempts':0},{'budget_turns':True},{'keys':['w']}):
            v=proposal();v.update(patch)
            with self.assertRaises(ValueError):validate_plan(v)
    def test_item_disposition_needs_reason_and_cannot_claim_pickup_or_capacity(self):
        v=proposal();v['item_decisions']=[{'cell':[2,1],'decision':'defer','reason':'所持品の交換候補を確認する','revisit':'inventory_changed'}]
        self.assertEqual(validate_plan(v)['item_decisions'][0]['decision'],'defer')
        for change in ({'decision':'pickup_confirmed'},{'reason':''},{'cell':[100,1]},{'revisit':'never'}):
            bad=copy.deepcopy(v);bad['item_decisions'][0].update(change)
            with self.assertRaises(ValueError):validate_plan(bad)

    def test_cell_animation_does_not_change_domain_but_floor_controls_and_contact_do(self):
        l=ledger();scope=l.current['scope'];s=state();s['minimap']['player_cell']=[100,100]
        l.observe(native('1階 HP15/15'),s,{'location':'dungeon'},{},{'revision':0},0,1);self.assertEqual(l.current['scope'],scope)
        for change_kind in ('floor','contact','revision','guidance'):
            l=ledger();s=state()
            if change_kind=='floor':s['metrics']['floor']['values']=[2]
            if change_kind=='contact':s['minimap']['enemies']=[(0,1)]
            old=l.current['scope'];l.observe(native(),s,{'location':'dungeon'},{},{'revision':int(change_kind=='guidance')},int(change_kind=='revision'),1)
            self.assertNotEqual(l.current['scope'],old)
    def test_blocked_review_is_async_single_request_and_does_not_block_local_turns(self):
        entered=threading.Event();release=threading.Event();calls=[]
        def review(snapshot,image,cancel):
            calls.append(snapshot);entered.set();release.wait(2);return proposal()
        sup=Supervisor(review,threading.Event());self.addCleanup(sup.close);self.addCleanup(release.set)
        l=ledger();plan=sup.tick(l);self.assertEqual(plan['origin'],'local_bootstrap');self.assertTrue(entered.wait(1))
        for step in range(1,6):l.current['step']=step;self.assertIsNotNone(sup.tick(l))
        self.assertEqual(len(calls),1);release.set();reply_ready(sup);sup.tick(l)
        self.assertEqual(sup.plan['origin'],'llm_supervisor');self.assertEqual(sup.adoptions,1)
    def test_interface_plan_is_rejected_without_current_ui_evidence(self):
        def review(*args):
            value=proposal();value.update(skill='interface');return value
        sup=Supervisor(review,threading.Event());self.addCleanup(sup.close)
        l=ledger();sup.tick(l);reply_ready(sup);plan=sup.tick(l)
        self.assertEqual(sup.adoptions,0);self.assertEqual(plan['origin'],'local_bootstrap')
        self.assertIn('根拠がない',sup.last_error)

    def test_late_floor_or_guidance_reply_is_discarded(self):
        release=threading.Event()
        def review(*args):release.wait(2);return proposal()
        sup=Supervisor(review,threading.Event());self.addCleanup(sup.close);self.addCleanup(release.set)
        l=ledger();sup.tick(l);release.set();reply_ready(sup)
        s=state();s['metrics']['floor']['values']=[2];new=ledger(s,1)
        sup.tick(new);self.assertEqual(sup.adoptions,0);self.assertEqual(sup.discarded,1)
    def test_reply_age_limit_and_close_prevent_stale_adoption(self):
        sup=Supervisor(lambda *a:proposal(),threading.Event());l=ledger();sup.tick(l);reply_ready(sup)
        l.current['step']=65;sup.tick(l);self.assertEqual(sup.discarded,1);sup.close();self.assertIsNone(sup.tick(l))
    def test_invalid_response_has_backoff_without_stopping_local_execution(self):
        sup=Supervisor(lambda *a:{'bad':'plan'},threading.Event());self.addCleanup(sup.close);l=ledger()
        sup.tick(l);reply_ready(sup);sup.tick(l);self.assertEqual(sup.errors,1);self.assertGreater(sup.next_review,0)
        sup.tick(l);self.assertIsNotNone(sup.plan);self.assertEqual(sup.requests,1)
    def test_recover_plan_survives_known_ui_phase_but_not_new_floor(self):
        sup=Supervisor(lambda *a:proposal(),threading.Event());self.addCleanup(sup.close);l=ledger()
        p=sup.bootstrap(l.current);p['skill']='recover';sup.plan=p;sup.next_review=100
        ui=StateLedger();s=state();s['interface_kind']='main_menu';s['menu_evidence']=True
        ui.observe(native('道具','マップ','足元','その他'),s,{'location':'dungeon'},{},{'revision':0},0,1)
        sup.tick(ui);self.assertEqual(sup.plan['skill'],'recover')
        s['metrics']['floor']['values']=[2];fresh=ledger(s,2);sup.tick(fresh)
        self.assertNotEqual(sup.plan['skill'],'recover')
    def test_current_battle_log_and_map_are_not_a_modal_menu(self):
        s=state();s['metrics_source_frame_sha256']='normal';s['white_frame_evidence']={'visible':True}
        l=StateLedger();n=dict(native('1階 HP15/15','トルネコはおおなめくじに4ポイントのダメージを与えた'),frame_sha256='normal')
        l.observe(n,s,{'location':'dungeon'},{},{'revision':0},0,0)
        self.assertIsNone(l.current['interface']);self.assertEqual(l.current['text_overlay_hypothesis'],'battle_message_hypothesis')
        n['frame_sha256']='old';l.observe(n,s,{'location':'dungeon'},{},{'revision':0},0,1)
        self.assertEqual(l.current['interface'],'text_frame')
    def test_retained_map_and_stats_are_not_current_facts(self):
        s=state();s['minimap'].update(retained=True,enemies=[(0,1)]);s['metrics']['strength']={'values':[8,8],'retained':True}
        current=ledger(s).current;self.assertFalse(current['map_current']);self.assertEqual(current['contacts'],[])
        self.assertIsNone(current['player_cell']);self.assertNotIn('strength',current['metrics'])
    def test_stop_cancel_is_visible_to_worker(self):
        stop=threading.Event();started=threading.Event();finished=threading.Event()
        def review(snapshot,image,cancel):
            started.set();stop.wait(1);self.assertTrue(cancel.is_set());finished.set();return proposal()
        sup=Supervisor(review,stop);sup.tick(ledger());self.assertTrue(started.wait(1));stop.set();self.assertTrue(finished.wait(1));sup.close();self.assertIsNone(sup.tick(ledger()))

class EffectsAndAuthorityTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.path=Path(t.name)/'m.sqlite';self.cfg=Settings()
        self.memory=MemoryStore(self.path,self.cfg);self.addCleanup(self.memory.close);self.effects=ActionEffects(self.memory,self.cfg)
        self.actor=OperationalControl(self.cfg,self.effects);self.mapless=MaplessNavigation(self.cfg);self.exterior=ExteriorActions(self.cfg,self.mapless)
        self.s=state();self.l=ledger();self.plan=Supervisor(lambda *a:proposal(),threading.Event()).bootstrap(self.l.current)
    def base(self):return Decision('gameplay','','','',.8,'continue',[],80,600)
    def select(self,s=None,n=None,plan=None,combat=None,policy=None):
        s=s or self.s;n=n or native('1階 HP15/15');self.exterior.observe(s,n,'gameplay');s['exterior_action_policy']=self.exterior.context
        l=ledger(s);l.observe(n,s,self.exterior.context,{}, {'revision':0},0,0)
        return self.actor.select(self.base(),s,n,l,plan or self.plan,policy,None,combat,self.mapless,self.exterior,[],True,{})
    def test_empirical_movement_effects_transfer_between_cells_not_equipment_combat(self):
        after=state('B',261.7);row=receipt_row('d')
        for _ in range(3):self.effects.record(row,self.s,after,{})
        other=state('elsewhere',600.);self.assertEqual(self.effects.estimate(other,['d'])['samples'],3)
        blocked=copy.deepcopy(self.s);blocked['minimap']['enemies']=[(0,1)]
        self.assertEqual(self.effects.estimate(blocked,['d'])['samples'],0)
        self.assertEqual(ActionEffects(self.memory,self.cfg).estimate(other,['d'])['samples'],3)
    def test_diagnostic_abort_wrong_receipt_and_mode_mismatch_do_not_prove_movement(self):
        after=state('B',261.7)
        for patch in ({'event':'diagnostic'},{'turn_kind':'aborted'},{'input_receipt':{}},{'executed':False}):
            self.assertEqual(self.effects.record(receipt_row('d',**patch),self.s,after)['status'],'excluded')
        after['observation_mode']='num7_held';self.effects.record(receipt_row('d'),self.s,after)
        self.assertEqual(self.effects.estimate(self.s,['d'])['samples'],0)
    def test_orientation_is_preparation_not_ineffective_movement(self):
        result=self.effects.record(receipt_row(observed_keys=['num5','s']),self.s,self.s)
        self.assertEqual(result['role'],'orientation_candidate');self.assertIsNone(result['effects']['no_effect']);self.assertEqual(result['effects']['value'],0)
        self.assertTrue(self.effects.record(receipt_row('w'),self.s,self.s)['effects']['no_effect'])
    def test_natural_movement_does_not_train_input_displacement(self):
        self.effects.record(receipt_row('d'),self.s,state('B',261.7),{'position':1})
        self.assertEqual(self.effects.estimate(self.s,['d'])['samples'],0)
    def test_experiment_wording_cannot_reset_budget_and_unknown_is_not_failure(self):
        p=proposal();p.update(test='movement_response',hypothesis='移動しない理由を調べる',test_attempts=2)
        e=self.effects.experiment(p,self.s)
        self.effects.record_experiment(e,{'comparable':False,'expected_signal_observed':False},receipt_row())
        p['hypothesis']='別の言い方';e=self.effects.experiment(p,self.s)
        result=self.effects.record_experiment(e,{'comparable':True,'expected_signal_observed':False},receipt_row())
        self.assertTrue(result['exhausted']);self.assertEqual(result['attempts'],2);self.assertEqual(result['inconclusive'],1);self.assertEqual(result['contradictions'],1)
        self.assertEqual(ActionEffects(self.memory,self.cfg).experiment(p,self.s)['attempts'],2)
    def test_normal_choice_and_destructive_confirmation_are_not_generic_probes(self):
        d,_,_,_,op=self.select(n=native('やりなおしますか？','いいえ','▶はい'))
        self.assertEqual(d.keys,['num1']);self.assertEqual(op['selected_source'],'interface')
        d,*_=self.select(n=native('セーブデータを削除しますか？','▶はい','いいえ'));self.assertEqual(d.keys,['num2'])
    def test_enemy_and_item_routes_beat_plan_to_inspect(self):
        from autoplayer.policy import ExperiencePolicy
        policy=ExperiencePolicy(self.memory,self.cfg)
        p=copy.deepcopy(self.plan);p['skill']='inspect_inventory'
        for kind,priority in [('enemy',70),('items',60),('frontier',50)]:
            s=state();s['map_route_hint']={'key':'d','mode':'observed_grid_route','distance':2,'target_kind':kind}
            d,_,_,_,op=self.select(s=s,plan=p,policy=policy)
            self.assertEqual(d.keys,['d']);self.assertEqual(op['selected_priority'],priority)
    def test_inspection_attempt_is_not_repeated_when_scope_changes(self):
        p=copy.deepcopy(self.plan);p['skill']='inspect_inventory'
        s=state();s['retained_state']={'revision':7,'refresh_reasons':['pickup']}
        d,_,_,_,op=self.select(s=s,plan=p);self.assertEqual(op['selected_source'],'inventory_inspection')
        self.actor.last_inspection_step=-1000 # Even after cooldown, same revision is already attempted.
        d,_,_,_,op=self.select(s=s,plan=p);self.assertNotEqual(op['selected_source'],'inventory_inspection')
    def test_checked_inventory_does_not_reopen_from_main_menu(self):
        p=copy.deepcopy(self.plan);p['skill']='inspect_inventory'
        self.select(n=native('道具','詳細表示切替','整理','3/20','▶薬草'),plan=p)
        d,*_=self.select(n=native('▶道具','マップ','足元','その他'),plan=p)
        self.assertEqual(d.keys,['num2'])

    def test_purposeful_diagonal_route_dispatches_both_direction_keys(self):
        s=state();s['map_route_hint']={'key':'s+d','keys':['s','d'],'diagonal':True,'mode':'observed_grid_route','distance':2,'target_kind':'items'}
        d,choice,_,_,op=self.select(s=s)
        self.assertEqual(d.keys,['s','d']);self.assertEqual(op['selected_priority'],60)
        self.assertEqual(choice['exploration_route']['keys'],['s','d'])
        self.assertEqual(self.effects.role(d.keys,s),'movement_candidate')
        self.assertEqual(self.effects.role(d.keys,{**s,'_operational_interface':'main_menu'}),'chord')

    def test_ineffective_movement_is_not_reintroduced_by_other_candidates(self):
        from autoplayer.policy import ExperiencePolicy
        policy=ExperiencePolicy(self.memory,self.cfg);s=state()
        s['floor_exploration']={'ineffective_actions_here':[['w'],['s'],['a'],['d']]}
        d,_,_,_,op=self.select(s=s,policy=policy)
        self.assertEqual(op['selected_source'],'bounded_probe');self.assertEqual(d.keys,['num1'])

    def test_supplied_interface_plan_does_not_replace_visible_item_route(self):
        from autoplayer.world_model import WorldModel
        fixture=json.loads((Path(__file__).parent/'fixtures/unfounded-interface-1792963799451305500.json').read_text())
        s=fixture['state'];s['map_route_hint']=WorldModel().navigation_hint(s)
        self.assertEqual(s['map_route_hint']['target_kind'],'items')
        d,_,_,_,op=self.select(s=s,n=fixture['native'],plan=fixture['plan'])
        self.assertEqual(op['selected_source'],'floor_navigation');self.assertNotEqual(d.keys,['num1'])
    def test_actual_dialogue_still_uses_confirm_even_with_explore_plan(self):
        s=state();s['eligible']=False;s['white_frame_evidence']={'visible':True}
        d,_,_,_,op=self.select(s=s,n=native('ここは不思議のダンジョンです。'))
        self.assertEqual(op['selected_source'],'interface');self.assertEqual(d.keys,['num1'])

    def test_unknown_screen_role_hint_is_once_without_unobserved_dialogue_fallback(self):
        s=state();s.update(eligible=False,frame_sha256='frame');s['minimap']={'status':'unavailable'}
        n=native('不明な案内');n['frame_sha256']='frame';l=ledger(s)
        l.observe(n,s,{'location':'unclassified'},{},{},0,0)
        p=copy.deepcopy(self.plan);p.update(skill='interface',interface_role='confirm',interface_quote='不明な案内',request_text_key=l.current['text_key'],request_frame='frame')
        first=self.actor.interface(self.base(),n,s,p,l,[])
        self.assertEqual(first.keys,['num1']);self.assertTrue(p['hint_consumed'])
        self.assertIsNone(self.actor.interface(self.base(),n,s,p,l,[]))

    def test_explicit_floor_return_route_is_not_replaced_by_probe(self):
        s=state();s['minimap']['player_cell']=[43,4]
        s['map_route_hint']={'key':'a','mode':'backtrack_to_frontier','distance':66,'reason':'踏破済み経路で別の先端へ戻る'}
        s['floor_exploration']={'exhausted_branch_cells':[[44,4]],'goal':{'phase':'explore_frontier'}}
        from autoplayer.policy import ExperiencePolicy
        policy=ExperiencePolicy(self.memory,self.cfg)
        d,choice,_,_,op=self.select(s=s,policy=policy)
        self.assertEqual(d.keys,['a']);self.assertEqual(op['selected_source'],'floor_navigation')
        self.assertEqual(choice['source'],'floor_navigation');self.assertEqual(choice['exploration_route']['distance'],66)

    def test_policy_route_is_not_replaced_by_coverage_sweep(self):
        from autoplayer.policy import ExperiencePolicy
        policy=ExperiencePolicy(self.memory,self.cfg)
        d,choice,_,_,op=self.select(policy=policy)
        self.assertIn(d.keys,[['w'],['a'],['s'],['d']]);self.assertEqual(op['authority'],'operational_control');self.assertEqual(op['selected_source'],'policy')
        self.assertFalse(any(c['source']=='information_collection' for c in op['candidates']))
    def test_combat_orientation_and_attack_remain_one_bounded_skill(self):
        self.s['minimap']['enemies']=[(0,1)];c=CombatExperiments(self.memory,self.cfg)
        d,_,trial,_,op=self.select(combat=c);self.assertEqual(d.keys,['num5','s'])
        c.record(trial,receipt_row(observed_keys=d.keys,policy_state=self.s,policy_after=self.s),{})
        d,_,trial,_,op=self.select(combat=c);self.assertEqual(d.keys,['num1']);self.assertEqual(op['skill'],'combat')
    def test_item_action_menu_without_confirmed_item_context_does_not_drink(self):
        n=native('薬草','飲む','食べる','投げる','置く','説明');p=copy.deepcopy(self.plan);p['skill']='recover'
        d,*_=self.select(n=n,plan=p);self.assertEqual(d.keys,['num2'])
    def test_replaced_input_is_not_credited_to_operational_experiment(self):
        row=receipt_row('a',operation={'selected_keys':['w'],'expected_signal':'position'},action_effect_learning={'effects':{'moved':True,'comparable':True}})
        r=self.actor.feedback(row);self.assertFalse(r['selection_applied']);self.assertFalse(r['progress'])

    def test_natural_regeneration_is_not_credited_as_input_healing(self):
        before=state();before['metrics']['hp']['values']=[10,15]
        after=copy.deepcopy(before);after['metrics']['hp']['values']=[11,15]
        row=receipt_row('w')
        result=self.effects.record(row,before,after,{'metric_changes':{'hp':{'before':[9,15],'after':[10,15]}}})
        self.assertEqual(result['effects']['hp_change'],1);self.assertEqual(result['effects']['hp_gain_net'],0)
    def test_revisiting_a_cell_is_not_new_exploration_progress(self):
        row=receipt_row('w',operation={'selected_keys':['w'],'expected_signal':'position'},action_effect_learning={'effects':{'moved':True,'comparable':True}},policy_learning={'arrival_visits_before':3})
        result=self.actor.feedback(row);self.assertTrue(result['expected_signal_observed']);self.assertFalse(result['progress'])
    def test_stable_hud_without_map_does_not_prove_movement_failed(self):
        s=state();s['eligible']=False;s['minimap']={'status':'unavailable'};s['text_reliable']=True
        result=self.effects.record(receipt_row('w'),s,s)
        self.assertFalse(result['effects']['comparable']);self.assertIsNone(result['effects']['no_effect'])
    def test_map_sampling_text_is_not_used_for_menu_effect(self):
        before=state();before['observation_mode']='num7_held';before['frame_sha256']='before-map';before['metrics_source_frame_sha256']='before-normal'
        after=copy.deepcopy(before);after.update(eligible=False,observation_mode='normal',frame_sha256='after-normal',metrics_source_frame_sha256='after-normal')
        row=receipt_row('num0',ocr=dict(native('1階 HP15/15'),frame_sha256='before-normal'))
        result=self.effects.record(row,before,after,{},dict(native('道具','マップ','足元','その他'),frame_sha256='after-normal'))
        self.assertTrue(result['effects']['text_changed']);self.assertTrue(row['operational_text_pair']['usable'])
    def test_selected_no_without_geometry_is_not_confirmed_or_cancelled(self):
        d,*_=self.select(n=native('▶いいえ','はい','やりなおしますか？'))
        self.assertIn(d.keys[0],self.effects.keys('move'));self.assertNotIn(d.keys[0],('num1','num2'))
    def test_inventory_confirmation_arms_only_the_exact_recovery_item(self):
        s=state();s['metrics']['hp']['values']=[3,15];current=ledger(s).current
        n=native('道具','詳細表示切替','整理','1/20','▶薬草')
        key=self.actor._recovery_item(n,s,'inventory',current);self.assertEqual(key,'num1')
        row=receipt_row('num1',step=1,operation={'selected_keys':['num1'],'selected_source':'interface','expected_signal':'hp_gain'})
        self.actor.feedback(row);self.assertEqual(self.actor.item_context['phase'],'actions')
        current['step']=1
        key=self.actor._recovery_item(native('▶飲む','投げる','置く','説明'),s,'item_actions',current)
        self.assertEqual(key,'num1');self.actor.feedback(row);self.assertIsNone(self.actor.item_context)
    def test_item_context_uses_current_quote_and_is_cleared_by_cancel(self):
        s=state();s['metrics']['hp']['values']=[3,15];current=ledger(s).current
        n=native('道具','詳細表示切替','整理','1/20','▶薬草');n['_current_inventory_rows']=[{'display_name':'薬草','quote':'▶薬草'}]
        self.assertEqual(self.actor._recovery_item(n,s,'inventory',current),'num1')
        self.actor.feedback(receipt_row('num1',step=1,operation={'selected_keys':['num1'],'selected_source':'interface'}))
        self.assertIsNotNone(self.actor.item_context)
        self.actor.feedback(receipt_row('num2',step=2,operation={'selected_keys':['num2'],'selected_source':'interface'}))
        self.assertIsNone(self.actor.item_context)
    def test_outside_never_explores_menu_face_arrow_or_footstep(self):
        s={'metrics':{},'minimap':{}}
        for _ in range(12):
            d,*_=self.select(s=s,n=native())
            self.assertIn(d.keys,[['w'],['a'],['s'],['d'],['num1']])
    def test_completion_plan_needs_a_current_completion_quote(self):
        p=copy.deepcopy(self.plan);p.update(skill='verify_goal',interface_quote='箱を持ち帰った')
        d,*_=self.select(n=native('箱を持ち帰った'),plan=p);self.assertEqual(d.status,'complete')
        d,*_=self.select(n=native('1階 HP15/15'),plan=p);self.assertEqual(d.status,'continue')

class SupervisedDispatchTests(unittest.TestCase):
    def test_controller_keeps_inputs_running_while_supervisor_waits(self):
        release=threading.Event();started=threading.Event()
        class Client(Script):
            def supervise(self,snapshot,image,cancel):started.set();release.wait(2);return proposal()
            def decide(self,*args):raise AssertionError('No per-turn LLM call in supervision mode')
        class Receipt(Backend):
            def act(self,keys,ms,stop):
                super().act(keys,ms,stop)
                return {'os_submission':'accepted','events':[{'message':'WM_KEYDOWN'}]}
        with tempfile.TemporaryDirectory() as t:
            cfg=Settings(max_steps=3);memory=MemoryStore(Path(t)/'m.sqlite',cfg);client=Client([]);backend=Receipt()
            try:
                client.ocr=SimpleNamespace(read=Mock(return_value=native('1階 HP15/15')))
                agent=Controller(cfg,backend,client,lambda _:None,Path(t)/'run',memory=memory)
                agent.policy.observe=Mock(return_value=state());agent.run()
                self.assertTrue(started.is_set());self.assertEqual(len(backend.actions),3)
                rows=[json.loads(line) for line in agent.journal.read_text().splitlines()]
                results=[r for r in rows if r.get('event')=='result'];self.assertEqual(len(results),3)
                self.assertTrue(all(r['operation']['authority']=='operational_control' for r in results))
                self.assertTrue(all(not r['performance']['llm_requested'] for r in results));self.assertTrue(all('action_effect_learning' in r for r in results))
                self.assertFalse(any(r.get('active_input',{}).get('source')=='information_collection' for r in results))
                predictions=[r for r in rows if r.get('event')=='action_prediction']
                self.assertEqual(len(predictions),3)
                self.assertTrue(all('action_prediction' in r and 'prediction_validation' in r for r in results))
                for r in results:
                    frozen=next(p for p in predictions if p['step']==r['step'])
                    self.assertEqual(frozen['prediction']['keys'],r['observed_keys'])
                    self.assertLess(rows.index(frozen),next(i for i,v in enumerate(rows) if v.get('event')=='sent' and v['step']==r['step']))
                self.assertEqual(backend.releases,1)
            finally:release.set();memory.close()
    def test_settings_can_restore_legacy_mode_and_reject_invalid_flag(self):
        self.assertTrue(Settings().supervised_learning)
        self.assertFalse(Settings(supervised_learning=False).validate().supervised_learning)
        with self.assertRaises(ValueError):Settings(supervised_learning='yes').validate()

class SupervisorRequestTests(unittest.TestCase):
    def test_supervision_sends_functions_evidence_knowledge_and_japanese_plan_schema(self):
        from unittest.mock import patch
        client=LocalLLM(Settings());snapshot=ledger().snapshot()
        with patch.object(LocalLLM,'_request',autospec=True,return_value=json.dumps(proposal())) as request:
            result=client.supervise(snapshot,None,threading.Event())
        payload=request.call_args.args[3]
        self.assertEqual(payload['task'],'operational_supervision');self.assertIn('available_functions',payload);self.assertIn('game_guide',payload)
        self.assertEqual(result['navigation'],'current_goal');self.assertNotIn('keys',result)
        other=LocalLLM(Settings(window_title='Other',allowed_keys=['z','x']))
        with patch.object(LocalLLM,'_request',autospec=True,return_value=json.dumps(proposal())) as request:
            other.supervise(snapshot,None,threading.Event())
        self.assertEqual(set(request.call_args.args[3]['current_key_bindings']),{'z','x'})
    def test_navigation_preference_needs_an_observed_target(self):
        from autoplayer.world_model import WorldModel
        w=WorldModel();s=state();s['minimap'].update(player_cell=[1,1],accumulated_cells=[[1,1],[2,1],[3,1]],stairs=[[2,0]],items=[])
        self.assertEqual(w.navigation_hint(s,'stairs')['target_cell'],[3,1])
        s['minimap']['stairs']=[]
        hint=w.navigation_hint(s,'stairs');self.assertTrue(hint is None or hint['target_cell'] in [[1,1],[2,1],[3,1]])
    def test_plan_metrics_distinguish_preparation_and_progress(self):
        sup=Supervisor(lambda *a:proposal(),threading.Event());self.addCleanup(sup.close)
        sup.observe_result({'executed':True,'step':1,'operation':{'plan_id':'a','skill':'combat'},'operation_evaluation':{'progress':False,'preparation':True},'forecast_evaluation':{'status':'matched'},'performance':{'total_ms':10}})
        metric=sup.plan_stats['a'];self.assertEqual(metric['preparations'],1);self.assertEqual(metric['progress_observations'],0);self.assertEqual(metric['prediction_matches'],1)
    def test_telemetry_uses_supervisor_review_interval(self):
        from autoplayer.telemetry import RecognitionState
        t=RecognitionState();t.update({'event':'result','forecast_evaluation':{'status':'mismatch'},'supervision':{'review_running':False,'review_in':12}})
        self.assertEqual(t.remaining,12)
        t.update({'event':'result','supervision':{'review_running':True,'review_in':12}});self.assertEqual(t.remaining,0)
    def test_visual_cursor_uses_only_the_current_frame(self):
        import hashlib
        from unittest.mock import patch
        image=b'cursor-contract';n=dict(native('道具','マップ','足元','その他'),frame_sha256=hashlib.sha256(image).hexdigest())
        with patch('autoplayer.world_model.Inventory.cursor_line',return_value='道具') as cursor:
            result=Controller._operational_ocr(n,image,{})
            self.assertEqual(result['_visual_selection']['text'],'道具');self.assertNotIn('_visual_selection',n)
            self.assertIs(Controller._operational_ocr(n,b'other-current-frame',{}),n);self.assertEqual(cursor.call_count,1)
    def test_expensive_review_summary_runs_only_when_requesting_on_owner_thread(self):
        owner=threading.get_ident();called=[];gate=threading.Event()
        def extra():called.append(threading.get_ident());return {'summary':'観測の要約'}
        def review(*args):gate.wait(1);return proposal()
        sup=Supervisor(review,threading.Event());self.addCleanup(sup.close);self.addCleanup(gate.set)
        l=ledger();sup.tick(l,extra);sup.tick(l,extra)
        self.assertEqual(called,[owner])
    def test_operational_effects_have_an_independent_bounded_export(self):
        import zipfile
        from autoplayer.diagnostics import export_diagnostics
        with tempfile.TemporaryDirectory() as t:
            folder=Path(t);m=MemoryStore(folder/'memory.sqlite',Settings());effects=ActionEffects(m,Settings())
            effects.record(receipt_row('w'),state(),state())
            effects.experiment(dict(proposal(),test='movement_response',hypothesis='移動作用を確認'),state())
            from autoplayer.experience_cycle import ExperienceCycle
            cycle=ExperienceCycle(effects);s=state();r=receipt_row('w',action_prediction=cycle.predict(s,['w']))
            r['action_effect_learning']=effects.record(r,s,s);cycle.record(r,s,s)
            m.close();destination=folder/'report.zip';export_diagnostics(folder,destination)
            with zipfile.ZipFile(destination) as z:
                exported=json.loads(z.read('supervision/learning.json'));self.assertEqual(exported['action_effects_v1']['total'],1)
                self.assertEqual(exported['action_effects_v1']['recent'][0]['action'],['w'])
                self.assertTrue(exported['experience_rules_v1']['recent'])
                cycle=json.loads(z.read('supervision/experience-cycle.json'));self.assertTrue(cycle['rules']);self.assertTrue(cycle['recent_checks'])
