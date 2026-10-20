"""Real-message grammar, partial observations, and history boundaries; no game."""
import copy
import hashlib
import json
import unittest
from autoplayer.battle_messages import read_messages
from autoplayer.current_evidence import historical,current
from autoplayer.knowledge import KnowledgeEngine
import test_combat_results as combat_tests
native=combat_tests.native
from test_policy import receipt_row

class BattleObservationTests(unittest.TestCase):
    setUp=combat_tests.CombatTests.setUp
    def test_explicit_kill_survives_unread_hp_and_missing_map(self):
        _,trial=self.combat.choose(self.decision,self.s,True)
        trial.update(stage='attack_trial',keys=['num1'])
        after=copy.deepcopy(self.s);after['eligible']=False;after['metrics'].pop('hp');after['minimap']={'status':'unavailable'}
        message=native(['HP41/-43','トルネコは おおなめくじに','13ポイントのダメージを与えた。','おおなめくじをやっつけた。'])
        row=receipt_row(observed_keys=['num1'],policy_state=self.s,policy_after=after,ocr=native(['HP41/43']))
        row['battle_observations']=self.combat.observe_messages(row,row['ocr'],[(message,message['frame_sha256'])])
        result=self.combat.record(trial,row,message)
        self.assertEqual(result['status'],'updated');self.assertFalse(result['hp_comparable']);self.assertGreater(result['reward'],0)
        self.assertEqual({e['kind'] for e in row['battle_observations']},{'damage_dealt','defeated_enemy'})
        self.assertEqual(row['battle_observations'][0]['enemy'],'おおなめくじ')
        self.assertEqual(row['battle_observations'][0]['damage'],13)
        self.combat.observe_messages(row,message,[(message,message['frame_sha256'])])
        self.assertEqual(self.memory.db.execute('SELECT COUNT(*) FROM combat_observations').fetchone()[0],2)
        self.assertEqual(read_messages(message,'different-frame'),[])
        self.assertTrue(self.combat.experience()['observations'])

    def test_chased_movement_and_marker_loss_are_not_combat_success(self):
        _,trial=self.combat.choose(self.decision,self.s,True);trial.update(stage='movement_trial',keys=['a'],action='step_a')
        after=copy.deepcopy(self.s);after['minimap']['player_cell'][0]-=1
        row=receipt_row(observed_keys=['a'],policy_state=self.s,policy_after=after)
        self.assertLess(self.combat.record(trial,row,{})['reward'],0)
        after['minimap']['enemies']=[]
        self.assertLess(self.combat.record(trial,row,{})['reward'],0)
        after['minimap']['enemies']=[[-2,0]]
        self.assertTrue(self.combat.record(trial,row,{})['separation_observed'])

    def test_old_positive_scores_do_not_select_new_tactics(self):
        context=json.dumps(['cardinal-contact-v1','a',sorted(self.cfg.allowed_keys)])
        with self.memory.db:self.memory.db.execute('INSERT INTO combat_actions VALUES(?,?,?,100,1000)',(self.memory.profile,context,'step_a'))
        _,trial=self.combat.choose(self.decision,self.s,True)
        self.assertEqual(trial['stage'],'orient_trial');self.assertIn('v2',trial['context'])

class CurrentBoundaryTests(unittest.TestCase):
    def test_historical_values_removed_without_mutating_saved_experience(self):
        original={'provisional_claims':[{'key':'HP','value':'10/15'}],'observation':'HPは10/15、満腹度は55%、3階',
            'recorded_hp':[10,15],'metrics':{'hp':[10,15]},'keys':['a']}
        filtered=historical(original)
        self.assertNotIn('10/15',json.dumps(filtered,ensure_ascii=False));self.assertNotIn('55%',json.dumps(filtered,ensure_ascii=False))
        self.assertEqual(original['recorded_hp'],[10,15]);self.assertEqual(filtered['keys'],['a'])
        png=b'fresh';reading=native(['HP43/43','5階'],png)
        now=current(png,reading,KnowledgeEngine())
        self.assertEqual(now['metrics']['hp']['values'],[43,43]);self.assertIn('shield',now['unknown_fields'])
        unknown=current(b'other',reading,KnowledgeEngine())
        self.assertEqual(unknown['metrics'],{});self.assertEqual(unknown['ocr_status'],'frame_mismatch')

    def test_planner_receives_latest_metrics_and_redacted_historical_values(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from autoplayer.core import LocalLLM,Settings
        from test_agent import change
        png=b'current';reading=native(['HP43/43','5階','満腹度30%'],png)
        client=LocalLLM(Settings());client.ocr=SimpleNamespace(read=Mock(return_value=reading))
        client.context={'provisional_claims':[{'key':'HP','value':'10/15'}],
            'autonomous_goal':{'reason':'HPは10/15なので回復','objective':'回復する'}}
        client.observe_scene=Mock(return_value={'scene_type':'gameplay','visible_text':['HP43/43'],
            'description':'木製の廊下にキャラクターがいる。','player_position':'中央','open_routes':['右'],
            'obstacles':[],'hazards':[],'confidence':.9})
        client._request=Mock(return_value=json.dumps(change(scene='gameplay',reason='廊下の右側の先を観察する。',subgoal='右側の通路を調べる',goal_success='右側に進んだ画面を確認する')))
        client.decide(png,[{'observation':'HPは10/15','keys':['a']}],False)
        prompt=client._request.call_args.args[2]
        self.assertEqual(prompt['current_evidence']['metrics']['hp']['values'],[43,43])
        self.assertNotIn('10/15',json.dumps(prompt,ensure_ascii=False))
        self.assertEqual(client.take_state()['current_evidence']['frame_sha256'],hashlib.sha256(png).hexdigest())
