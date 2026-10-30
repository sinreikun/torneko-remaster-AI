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
    def test_supplied_short_damage_messages_are_learned_without_inventing_enemy_name(self):
        from pathlib import Path
        data=json.loads((Path(__file__).parent/'fixtures/short-battle-1793010980478938000.json').read_text())
        self.s['metrics']['hp']['values']=data['before_hp'];self.s['minimap']['player_cell']=data['trial']['cell']
        self.s['minimap']['source_size']=data['trial']['frame_size'];self.s['minimap']['enemies']=[data['trial']['target']]
        after=copy.deepcopy(self.s);after['metrics']['hp']['values']=data['after_hp']
        row=receipt_row(observed_keys=['num1'],policy_state=self.s,policy_after=after,input_receipt=data['input_receipt'])
        events=self.combat.observe_messages(row,data['before'],[(data['after'],data['after']['frame_sha256'])])
        self.assertEqual([(e['kind'],e['damage']) for e in events],[('damage_dealt',2),('damage_received',3)])
        self.assertIsNone(events[0]['enemy']);self.assertEqual(events[1]['enemy'],'ゴースト')
        row['battle_observations']=events;result=self.combat.record(data['trial'],row,data['after'])
        self.assertGreater(result['reward'],-.44);self.assertIsNotNone(self.combat.pending)
        self.assertFalse(any(e['new_message'] for e in self.combat.observe_messages(row,data['after'],[(data['after'],data['after']['frame_sha256'])])))
        self.assertEqual(self.memory.db.execute('SELECT COUNT(*) FROM combat_observations').fetchone()[0],2)

    def test_short_grammar_does_not_duplicate_named_messages_or_read_other_actors(self):
        value=native(['トルネコはスライムに','2ポイントのダメージを与えた。','トルネコはスライムから','3ポイントのダメージを受けた。'])
        self.assertEqual(len(read_messages(value,value['frame_sha256'])),2)
        other=native(['スライムは2ポイントのダメージを与えた。'])
        self.assertEqual(read_messages(other,other['frame_sha256']),[])

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
        self.assertEqual(trial['stage'],'orient_trial');self.assertIn('v3-short-messages',trial['context'])

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

class ForcedCombatOCRTests(unittest.TestCase):
    setUp=combat_tests.CombatTests.setUp

    def test_forced_result_ocr_bypasses_fast_cache_and_restores_flags(self):
        from types import SimpleNamespace
        from pathlib import Path
        from autoplayer.core import Controller,Settings
        seen=[];png=b'combat-result'
        ocr=SimpleNamespace(force_fresh=False,fast_mode=True)
        def read(image):
            seen.append((image,ocr.force_fresh,ocr.fast_mode))
            return native(['トルネコの攻撃はスライムに当たらなかった'],image)
        ocr.read=read
        agent=Controller.__new__(Controller);agent.llm=SimpleNamespace(ocr=ocr);agent.policy=None;agent.cfg=Settings(fast_learning=True);agent.journal=Path('test-session.jsonl')
        row=receipt_row('num1',step=12)
        result=agent.read_action_result_ocr(png,row,{'eligible':True})
        self.assertEqual(seen,[(png,True,False)]);self.assertFalse(ocr.force_fresh);self.assertTrue(ocr.fast_mode)
        self.assertTrue(row['combat_message_ocr']['frame_linked']);self.assertEqual(row['combat_message_ocr']['step'],12)
        row.update(policy_learning={'status':'trained','reward':-.04},policy_state=self.s)
        events=self.combat.observe_messages(row,{},[(result,hashlib.sha256(png).hexdigest())])
        self.assertEqual(events[0]['kind'],'miss');self.assertEqual(events[0]['step'],12)
        saved=json.loads(self.memory.db.execute('SELECT data FROM combat_message_reads').fetchone()[0])
        self.assertEqual(saved['ocr']['lines'],result['lines']);self.assertEqual(saved['policy_learning']['reward'],-.04)
    def test_unparsed_and_failed_reads_are_preserved_without_hit_learning(self):
        for reading in (native(['戦闘文のOCRが崩れて読めない']),{'status':'unavailable','reason':'reader failed','lines':[]}):
            row=receipt_row('num1',combat_message_ocr={'step':3,'ocr':reading,'source_frame_sha256':'frame','frame_linked':False})
            self.assertEqual(self.combat.observe_messages(row,{},[(reading,'frame')]),[])
        self.assertEqual(self.memory.db.execute('SELECT COUNT(*) FROM combat_message_reads').fetchone()[0],2)
        self.assertEqual(self.memory.db.execute('SELECT COUNT(*) FROM combat_observations').fetchone()[0],0)
    def test_message_ocr_covers_contacts_and_item_actions_but_not_empty_movement(self):
        from autoplayer.combat import requires_message_ocr
        from autoplayer.core import Settings
        cfg=Settings();row=receipt_row('w')
        self.assertTrue(requires_message_ocr(row,{'minimap':{'enemies':[[1,0]]}},cfg))
        self.assertFalse(requires_message_ocr(row,{'eligible':True},cfg))
        self.assertTrue(requires_message_ocr(receipt_row('num1',ocr=native(['飲む','投げる','置く','説明'])),{'menu_evidence':True},cfg))
        self.assertFalse(requires_message_ocr(receipt_row('num1',dry_run=True),self.s,cfg))

    def test_windows_reader_also_honors_force_fresh_without_invoking_os(self):
        from autoplayer.ocr import WindowsOCR
        ocr=WindowsOCR();png=b'non-png-cache-contract';ocr.last_hash=hashlib.sha256(png).hexdigest()
        ocr.last={'status':'ok','lines':[{'text':'cached'}]}
        self.assertEqual(ocr.read(png)['status'],'ok')
        ocr.force_fresh=True
        result=ocr.read(png)
        self.assertEqual(result['status'],'unavailable');self.assertEqual(result['reason'],'OCR requires a PNG capture')
