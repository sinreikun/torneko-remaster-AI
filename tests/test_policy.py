"""Numerical/SQLite control contracts. No demo game, live input, or simulated gameplay."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from autoplayer.core import Settings,parse_decision,Controller
from autoplayer.controls import ACTIONS
from autoplayer.memory import MemoryStore
from autoplayer.policy import ExperiencePolicy
from test_agent import change,Script,Backend


def state(name='A',x=255.7,pixels=610):
    return {'id':name,'location':name,'observable':True,'visual':[0]*192,'text_key':'hud',
        'eligible':True,'objective':'explore','menu_evidence':False,'metrics':{'hp':{'values':[15,15]},'floor':{'values':[1]}},
        'minimap':{'status':'observed','confidence':.8,'shape':'shape','player':[x,134.],
            'bbox':[248,127,279,173],'cell_size':6,'player_cell':[round(x/6),22],
            'known_pixels':pixels,'enemies':[],'neighbors':{key:{'known':True,'enemy':False} for key in 'wasd'}}}

def receipt_row(key='w',**kw):
    return {'executed':True,'action_kind':'input','observed_keys':[key],'input_receipt':{'os_submission':'accepted'},**kw}

class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'memory.sqlite';self.cfg=Settings().validate()
        self.store=MemoryStore(self.path,self.cfg);self.addCleanup(lambda:self.store.close() if self.store else None)
        self.policy=ExperiencePolicy(self.store,self.cfg)
    def test_negative_experience_changes_executed_priority_and_survives_restart(self):
        before=state()
        for _ in range(4):self.policy.learn(before,before,receipt_row('w'),before)
        moved=state('B',261.7,650)
        self.policy.learn(before,moved,receipt_row('a'),before)
        # This assertion isolates persistent Q ranking from session route planning.
        self.policy.routes.reset()
        proposal=parse_decision(json.dumps(change(action={'keys':['w'],'hold_ms':80,'wait_ms':0})),self.cfg)
        selected,detail=self.policy.choose(proposal,before,stable=True)
        self.assertNotEqual(selected.keys,['w']);self.assertEqual(detail['source'],'learned_policy')
        selected_keys=selected.keys
        values=self.policy.values(before);self.assertEqual(values['w']['trials'],4)
        self.assertLess(values['w']['q'],values['a']['q'])
        self.assertIsNone(detail['selected']['verified_goal_success_probability'])
        self.store.close();self.store=MemoryStore(self.path,Settings(knowledge='編集後の攻略知識'))
        reopened=ExperiencePolicy(self.store,Settings(knowledge='編集後の攻略知識').validate())
        self.assertEqual(reopened.values(before),values)
        selected,_=reopened.choose(proposal,before,stable=True);self.assertEqual(selected.keys,selected_keys)
    def test_exploration_input_trains_outcomes_without_model_report_learning(self):
        before=state();after=state('B',261.7,650)
        row=receipt_row('w',learning=False,model_report_learning=False,active_input={'source':'information_collection'})
        event=self.policy.learn(before,after,row,before)
        self.assertEqual(event['status'],'trained');self.assertEqual(self.policy.values(before)['w']['trials'],1)
        diagnostic=dict(row,event='diagnostic')
        self.assertEqual(self.policy.learn(before,after,diagnostic,before)['status'],'not_trained')
    def test_animation_alone_is_not_positive_progress(self):
        before=state();before['eligible']=False
        after=state('B');after['eligible']=False;after['visual']=[255]*192
        result=self.policy.learn(before,after,receipt_row('w'),before)
        self.assertLessEqual(result['reward'],0)
    def test_return_trip_does_not_learn_a_profitable_loop(self):
        a,b=state('A'),state('B',261.7)
        first=self.policy.learn(a,b,receipt_row('w'),a)
        self.assertGreater(first['reward'],0)
        for _ in range(8):
            for before,after,key in ((b,a,'s'),(a,b,'w')):
                result=self.policy.learn(before,after,receipt_row(key),before)
                self.assertEqual(result['novelty_weight'],0)
                self.assertLess(result['reward'],0)
                self.assertFalse(result['positive_progress_proxy'])
        self.assertLess(self.policy.values(a)['w']['q'],0)
        self.assertLess(self.policy.values(b)['s']['q'],0)
        proposal=parse_decision(json.dumps(change(action={'keys':['w'],'hold_ms':80,'wait_ms':0})),self.cfg)
        self.assertNotEqual(self.policy.choose(proposal,a,stable=True)[0].keys,['w'])
        # A return with independently observed new coverage can still earn credit.
        expanded=state('A',pixels=1000)
        b['minimap']['known_cells']=[(0,0)]
        expanded['minimap']['known_cells']=[(x,y) for x in range(10) for y in range(10)]
        self.assertGreater(self.policy.learn(b,expanded,receipt_row('s'),b)['reward'],0)

    def test_natural_movement_does_not_get_input_progress_credit(self):
        initial=state('old',249.7,570);before=state('A',255.7,610);after=state('B',261.7,650)
        result=self.policy.learn(before,after,receipt_row(),initial)
        self.assertEqual(result['net_signals']['position'],0)
        self.assertLessEqual(result['reward'],0)
        self.assertEqual(result['attribution'],'observed_association_not_causal')
    def test_changed_shape_hash_does_not_reward_returning_to_session_location(self):
        a,b=state('A'),state('B',261.7)
        self.policy.learn(a,b,receipt_row('d'),a)
        changed=state('new-shape-A');changed['minimap']['shape']='marker-occlusion-changed-shape'
        result=self.policy.learn(b,changed,receipt_row('a'),b)
        self.assertEqual(result['novelty_weight'],0)
        self.assertLess(result['reward'],0)
    def test_new_scoring_namespace_keeps_old_data_but_does_not_select_it(self):
        with patch('autoplayer.policy.VERSION',3):
            old=ExperiencePolicy(self.store,self.cfg)
            old.learn(state(),state(),receipt_row(),state())
        self.assertNotEqual(old.profile,self.policy.profile)
        self.assertEqual(self.policy.values(state()),{})
        self.assertEqual(self.policy.db.execute('SELECT SUM(trials) FROM policy_actions WHERE profile=?',(old.profile,)).fetchone()[0],1)
    def test_observed_route_transit_is_not_rewarded_as_new_exploration_or_heavily_penalized(self):
        a,b=state('A'),state('B',261.7)
        self.policy.learn(a,b,receipt_row('d'),a)
        route={'source':'learned_policy','exploration_route':{'mode':'observed_route','distance':1,'key':'a'}}
        result=self.policy.learn(b,a,receipt_row('a',policy_choice=route),b)
        self.assertTrue(result['route_transit']);self.assertEqual(result['revisit_cost'],0)
        self.assertEqual(result['reward'],-.04);self.assertEqual(result['scoring_version'],10)
    def test_observation_diagnostics_dry_run_and_rejected_submission_do_not_train(self):
        for row in (receipt_row(executed=False),receipt_row(dry_run=True),receipt_row(event='diagnostic'),
                    receipt_row(learning=False),receipt_row(learning=False,policy_choice={'source':'learned_policy'}),receipt_row(turn_kind='aborted'),receipt_row(input_receipt={'os_submission':'rejected'})):
            self.assertEqual(self.policy.learn(state(),state(),row)['status'],'not_trained')
        self.assertEqual(self.policy.values(state()),{})
    def test_damage_penalty_and_no_full_hp_or_regeneration_reward(self):
        before=state();after=state();after['metrics']['hp']['values']=[12,15]
        result=self.policy.learn(before,after,receipt_row())
        self.assertLess(result['reward'],-.3)
        self.assertLessEqual(self.policy.learn(after,before,receipt_row())['reward'],0)
    def test_unstable_and_unavailable_are_guarded_but_game_danger_is_not_a_veto(self):
        proposal=parse_decision(json.dumps(change(action={'keys':[],'hold_ms':1,'wait_ms':0})),self.cfg)
        invalid=state();invalid['eligible']=False
        self.assertEqual(self.policy.choose(proposal,invalid,stable=True)[0].keys,[])
        enemy=state();enemy['minimap']['enemies']=[(1,0)]
        self.assertTrue(self.policy.choose(proposal,enemy,stable=True)[0].keys)
        dangerous=state();dangerous['objective']='survive'
        self.assertTrue(self.policy.choose(proposal,dangerous,stable=True)[0].keys)
        self.assertEqual(self.policy.choose(proposal,state(),stable=False)[0].keys,[])
        blocked=[[k] for k in 'wasd'];self.assertEqual(self.policy.choose(proposal,state(),blocked,True)[0].keys,[])
    def test_goal_probabilities_only_use_completed_verified_episodes(self):
        self.policy.learn(state(),state('B',261.7),receipt_row())
        self.assertEqual(self.policy.values(state())['w']['goal_trials'],0)
        self.policy.finish_verified({'achieved':False})
        self.assertEqual(self.policy.values(state())['w']['goal_trials'],0)
        q_before=self.policy.values(state())['w']['q']
        self.policy.finish_verified({'achieved':True,'evidence':['validated episode']})
        self.assertGreater(self.policy.values(state())['w']['q'],q_before)
        self.assertEqual(self.policy.values(state())['w']['goal_trials'],1)
        self.assertEqual(self.policy.values(state())['w']['goal_wins'],1)
        self.policy.finish_verified({'achieved':True})
        self.assertEqual(self.policy.values(state())['w']['goal_trials'],1)
        self.policy.learn(state(),state(),receipt_row());self.policy.finish_episode(False,{'heading':'ゲームオーバー'})
        self.assertEqual(self.policy.values(state())['w']['goal_trials'],2)
        self.assertEqual(self.policy.values(state())['w']['goal_wins'],1)
    def test_floor_transition_does_not_require_the_old_map_to_remain_visible(self):
        before=state();after=state('B');after['minimap']={'status':'unavailable'}
        after['metrics']['floor']['values']=[2]
        self.assertEqual(self.policy.signals(before,after)['floor'],1.)
    def test_food_gain_is_a_separate_observed_progress_signal(self):
        before=state();after=state('fed')
        before['metrics']['fullness']={'values':[10]};after['metrics']['fullness']={'values':[80]}
        result=self.policy.learn(before,after,receipt_row('num1'),before)
        self.assertAlmostEqual(result['net_signals']['food_gain'],.7)
        self.assertGreater(result['reward'],0)
    def test_changed_overall_goal_isolates_policy(self):
        self.policy.learn(state(),state(),receipt_row())
        other=ExperiencePolicy(self.store,Settings(goal='別の目標').validate())
        self.assertEqual(other.values(state()),{})
    def test_ineffective_nonmovement_can_be_reselected_but_dialog_and_combat_are_preserved(self):
        current=state()
        for _ in range(2):self.policy.learn(current,current,receipt_row('num1'),current)
        proposal=parse_decision(json.dumps(change(scene='gameplay',confidence=.5,action={'keys':['num1'],'hold_ms':80,'wait_ms':0})),self.cfg)
        chosen,detail=self.policy.choose(proposal,current,stable=True)
        self.assertIn(chosen.keys[0],'wasd');self.assertEqual(detail['reconsidered_nonmovement']['keys'],['num1'])
        proposal.scene='dialog'
        self.assertEqual(self.policy.choose(proposal,current,stable=True)[0].keys,['num1'])
        proposal.scene='gameplay';enemy=copy.deepcopy(current);enemy['minimap']['enemies']=[(1,0)]
        self.assertEqual(self.policy.choose(proposal,enemy,stable=True)[0].keys,['num1'])
        self.assertEqual(self.policy.choose(proposal,current,stable=False)[0].keys,['num1'])
    def test_marker_jitter_does_not_change_cell_but_real_movement_does(self):
        a=copy.deepcopy(state()['minimap']);a.update(player=[191.5,128.5],player_cell=[32,21])
        b=copy.deepcopy(a);b.update(player=[191.5,129.0],player_cell=[32,22],shape='marker-occlusion')
        tracked_a=self.policy.track_pose(a,[1]);tracked_b=self.policy.track_pose(b,[1])
        self.assertEqual(tracked_a['player_cell'],tracked_b['player_cell'])
        self.assertEqual(tracked_b['raw_player_cell'],[32,22]);self.assertEqual(b['player_cell'],[32,22])
        self.assertTrue(self.policy.stable_map(a,b))
        moved=copy.deepcopy(b);moved.update(player=[197.0,129.0],player_cell=[33,22])
        self.assertNotEqual(self.policy.track_pose(moved,[1])['player_cell'],tracked_a['player_cell'])
        self.assertEqual(self.policy.track_pose(moved,[1])['player_cell'],[33,21])
        self.assertFalse(self.policy.stable_map(a,moved))
        self.assertEqual(self.policy.track_pose(b,[2])['player_cell'],[32,22])
        first=copy.deepcopy(a);first.update(player=[213.,128.5],player_cell=[36,21])
        next_cell=copy.deepcopy(first);next_cell.update(player=[218.5,128.5],player_cell=[36,21])
        one=self.policy.track_pose(first,[3]);two=self.policy.track_pose(next_cell,[3])
        self.assertEqual(two['player_cell'],[one['player_cell'][0]+1,one['player_cell'][1]])
    def test_controller_executes_the_ranked_action_and_logs_actual_learning(self):
        before=state();after=state('B',261.7,650)
        # Receipt and features are mocked contracts, not evidence of game acceptance.
        class ReceiptBackend(Backend):
            def act(self,keys,ms,stop):
                super().act(keys,ms,stop)
                return {'os_submission':'accepted','events':[{'message':'WM_KEYDOWN'}]}
        backend=ReceiptBackend();backend.observe_turn=Mock(side_effect=[SimpleNamespace(image=b'current',kind='normal',changed=False,raw_change=0,masked_activity=0,duration_ms=1000),SimpleNamespace(image=b'after',kind='normal',changed=True,raw_change=.3,masked_activity=0,duration_ms=1000,last_activity_ms=100)])
        script=Script([change(scene='loading',action={'keys':[],'hold_ms':1,'wait_ms':0})])
        script.methods=SimpleNamespace(record=Mock())
        script.take_state=lambda:{'learning':False,'model_response_issue':'model report invalid','method_selection':{'method':'grounded_text'}}
        script.knowledge_engine=SimpleNamespace(goal=None)
        script.ocr=SimpleNamespace(read=Mock(return_value={'status':'ok','lines':[]}))
        agent=Controller(Settings(max_steps=1,adaptive_timing=True),backend,script,lambda row:None,Path(self.temp.name)/'run.jsonl',memory=self.store)
        agent.policy.observe=Mock(side_effect=[before,before,after]);agent.run()
        self.assertEqual(backend.actions[0][0],['w'])
        rows=[json.loads(line) for line in agent.journal.read_text().splitlines()]
        result=rows[-1];self.assertEqual(result['policy_choice']['source'],'learned_policy')
        self.assertEqual(result['policy_learning']['status'],'trained')
        self.assertEqual(result['input_coverage']['status'],'response_observed')
        self.assertEqual(result['llm_proposal']['keys'],[])
        self.assertEqual(result['observed_keys'],['w'])
        self.assertFalse(result['model_report_learning']);self.assertFalse(result['learning'])
        self.assertIn('黄色マーカー',result['observation'])
        self.assertEqual(script.knowledge_engine.goal['source'],'experience_policy')
        script.methods.record.assert_not_called()
        self.assertEqual(result['method_learning']['reason'],'model_action_replaced_by_map_policy')
        self.assertEqual(result['adaptive_pacing']['wait_ms'],600)
        self.assertEqual(result['pacing_evaluation']['status'],'stable_sample')
        self.assertEqual(backend.observe_turn.call_args_list[1].args[2],600)

class ConfirmedControlsTests(unittest.TestCase):
    def test_old_enter_space_settings_migrate_without_sending_them(self):
        cfg=Settings(allowed_keys=['w','enter','space','num1']).validate()
        self.assertEqual(cfg.allowed_keys,['w','num1'])
        for key in ('enter','space'):
            with self.assertRaises(ValueError):parse_decision(json.dumps(change(action={'keys':[key],'hold_ms':80,'wait_ms':0})),cfg)
        self.assertEqual(ACTIONS['num5'],'方向転換／整理')
        self.assertEqual(ACTIONS['num7'],'マップ表示')
    def test_minimap_cannot_be_an_approach_destination(self):
        from autoplayer.core import response_quality_issue
        wrong=parse_decision(json.dumps(change(subgoal='青い物体に近づいて話しかける')),Settings())
        self.assertIn('対象物ではありません',response_quality_issue(wrong,minimap_reference=True))
        reading=parse_decision(json.dumps(change(subgoal='ミニマップを確認して次の通路を選ぶ')),Settings())
        self.assertEqual(response_quality_issue(reading,minimap_reference=True),'')
        self.assertEqual(response_quality_issue(wrong,minimap_reference=False),'')
    def test_wrong_allowlist_is_still_rejected(self):
        for keys in (['alt'],[1],[]):
            with self.assertRaises(ValueError):Settings(allowed_keys=keys).validate()

class DiagnosticLearningArchiveTests(unittest.TestCase):
    def test_archive_keeps_matching_observation_and_result_and_policy_summary(self):
        import zipfile,os
        from autoplayer.diagnostics import export_diagnostics
        with tempfile.TemporaryDirectory() as folder:
            data=Path(folder);cfg=Settings().validate();memory=MemoryStore(data/'memory.sqlite',cfg)
            policy=ExperiencePolicy(memory,cfg);policy.learn(state(),state(),receipt_row())
            frames=data/'recordings'/'session';frames.mkdir(parents=True)
            manifest=[]
            for n,(name,phase) in enumerate((('observation.png','observation'),('result.png','result'),('end.png','session_end'),('pending.png','observation'))):
                path=frames/name;path.write_bytes(b'archive contract only');os.utime(path,(1700000000+n,1700000000+n));manifest.append({'file':name,'phase':phase})
            (frames/'frames.jsonl').write_text('\n'.join(json.dumps(x) for x in manifest))
            path=data/'report.zip';report=export_diagnostics(data,path)
            with zipfile.ZipFile(path) as archive:
                self.assertIn('recordings/session/observation.png',archive.namelist())
                self.assertIn('recordings/session/result.png',archive.namelist())
                self.assertNotIn('recordings/session/end.png',archive.namelist())
                self.assertNotIn('recordings/session/pending.png',archive.namelist())
            self.assertEqual(report['image_evidence']['mode'],'latest_complete_observation_result')
            self.assertEqual(report['databases']['memory.sqlite']['policy_summary'][0]['trials'],1)
            self.assertEqual(memory.context()['policy_learning']['input_trials'],1)
            memory.close()

class HudObservationTests(unittest.TestCase):
    setUp=PolicyTests.setUp
    def test_already_observed_hud_does_not_keep_unknown_confirm_loop(self):
        s=state();s['metrics']['fullness']={'values':[43]}
        d=parse_decision(json.dumps(change(scene='unknown',confidence=.5,subgoal='現在の場所でHPと満腹度を確認し、状況を把握する',
            goal_success='HPと満腹度を確認し、状況を把握する',action={'keys':['num1'],'hold_ms':100,'wait_ms':0})),self.cfg)
        selected,detail=self.policy.choose(d,s,stable=True)
        self.assertIn(selected.keys[0],'wasd');self.assertEqual(detail['reconsidered_nonmovement']['source'],'already_observed_hud')
        self.assertEqual(d.keys,['num1'])
        for altered in ('enemy','unstable','menu','missing_hud','dialogue'):
            other=copy.deepcopy(s);proposal=copy.deepcopy(d);stable=True
            if altered=='enemy':other['minimap']['enemies']=[[1,0]]
            if altered=='unstable':stable=False
            if altered=='menu':other['menu_evidence']=True
            if altered=='missing_hud':other['metrics'].pop('fullness')
            if altered=='dialogue':proposal.scene='dialogue'
            kept,_=self.policy.choose(proposal,other,stable=stable)
            self.assertEqual(kept.keys,['num1'],altered)

    def test_old_q_outliers_have_bounded_influence(self):
        s=state();self.policy.learn(s,s,receipt_row('w'),s)
        self.store.db.execute('UPDATE policy_actions SET q=1000');self.store.db.commit()
        ranking=self.policy.context(s)['ranking']
        self.assertTrue(any(r['q']==1000 for r in ranking))
        self.assertTrue(all(abs(r['historical_q_contribution'])<=.35 for r in ranking))
        self.assertTrue(all(abs(r['historical_backoff_contribution'])<=.1 for r in ranking))
