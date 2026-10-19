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
        proposal=parse_decision(json.dumps(change(action={'keys':['w'],'hold_ms':80,'wait_ms':0})),self.cfg)
        selected,detail=self.policy.choose(proposal,before,stable=True)
        self.assertEqual(selected.keys,['a']);self.assertEqual(detail['source'],'learned_policy')
        values=self.policy.values(before);self.assertEqual(values['w']['trials'],4)
        self.assertLess(values['w']['q'],values['a']['q'])
        self.assertIsNone(detail['selected']['verified_goal_success_probability'])
        self.store.close();self.store=MemoryStore(self.path,Settings(knowledge='編集後の攻略知識'))
        reopened=ExperiencePolicy(self.store,Settings(knowledge='編集後の攻略知識').validate())
        self.assertEqual(reopened.values(before),values)
        selected,_=reopened.choose(proposal,before,stable=True);self.assertEqual(selected.keys,['a'])
    def test_natural_movement_does_not_get_input_progress_credit(self):
        initial=state('old',249.7,570);before=state('A',255.7,610);after=state('B',261.7,650)
        result=self.policy.learn(before,after,receipt_row(),initial)
        self.assertEqual(result['net_signals']['position'],0)
        self.assertLessEqual(result['reward'],0)
        self.assertEqual(result['attribution'],'observed_association_not_causal')
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
    def test_unavailable_map_and_enemy_directions_are_not_autonomously_walked(self):
        proposal=parse_decision(json.dumps(change(action={'keys':[],'hold_ms':1,'wait_ms':0})),self.cfg)
        invalid=state();invalid['eligible']=False
        self.assertEqual(self.policy.choose(proposal,invalid,stable=True)[0].keys,[])
        enemy=state();enemy['minimap']['enemies']=[(1,0)]
        self.assertEqual(self.policy.choose(proposal,enemy,stable=True)[0].keys,[])
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
    def test_controller_executes_the_ranked_action_and_logs_actual_learning(self):
        before=state();after=state('B',261.7,650)
        # Receipt and features are mocked contracts, not evidence of game acceptance.
        class ReceiptBackend(Backend):
            def act(self,keys,ms,stop):
                super().act(keys,ms,stop)
                return {'os_submission':'accepted','events':[{'message':'WM_KEYDOWN'}]}
        backend=ReceiptBackend();backend.observe_turn=Mock(side_effect=[SimpleNamespace(image=b'current',kind='normal',changed=False,raw_change=0,masked_activity=0,duration_ms=1000),SimpleNamespace(image=b'after',kind='normal',changed=True,raw_change=.3,masked_activity=0,duration_ms=1000)])
        script=Script([change(scene='loading',action={'keys':[],'hold_ms':1,'wait_ms':0})])
        script.take_state=lambda:{'learning':False,'model_response_issue':'model report invalid'}
        script.knowledge_engine=SimpleNamespace(goal=None)
        script.ocr=SimpleNamespace(read=Mock(return_value={'status':'ok','lines':[]}))
        agent=Controller(Settings(max_steps=1),backend,script,lambda row:None,Path(self.temp.name)/'run.jsonl',memory=self.store)
        agent.policy.observe=Mock(side_effect=[before,before,after]);agent.run()
        self.assertEqual(backend.actions[0][0],['w'])
        rows=[json.loads(line) for line in agent.journal.read_text().splitlines()]
        result=rows[-1];self.assertEqual(result['policy_choice']['source'],'learned_policy')
        self.assertEqual(result['policy_learning']['status'],'trained')
        self.assertEqual(result['llm_proposal']['keys'],[])
        self.assertEqual(result['observed_keys'],['w'])
        self.assertFalse(result['model_report_learning']);self.assertTrue(result['learning'])
        self.assertIn('黄色マーカー',result['observation'])
        self.assertEqual(script.knowledge_engine.goal['source'],'experience_policy')

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
