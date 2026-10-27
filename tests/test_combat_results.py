"""Relative-coordinate, SQLite, and OCR-result contracts; no demo game."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from autoplayer.combat import CombatExperiments,relative_contacts
from autoplayer.core import Settings,LocalLLM,parse_decision,Controller
from autoplayer.interfaces import result_screen
from autoplayer.memory import MemoryStore
from autoplayer.performance import model_data,context_retry_prompt
from test_agent import Backend,Script,change
from test_policy import state,receipt_row


def native(labels,png=b'result'):
    return {'status':'ok','frame_sha256':hashlib.sha256(png).hexdigest(),'lines':[{'text':s} for s in labels]}


DEFEAT=native(['今 回 の 冒 険 の 結 果','最 深 階 1 階','ス コ ア 0G','地下1階でおおなめくじに倒される。'])
SCORES=native(['ハ イ ス コ ア','2 :','3 :','4 :','42 , 783G'])


class CombatTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.cfg=Settings();self.memory=MemoryStore(Path(self.temp.name)/'memory.sqlite',self.cfg);self.addCleanup(self.memory.close)
        self.combat=CombatExperiments(self.memory,self.cfg)
        self.s=state();self.s['minimap']['enemies']=[(-1,0)]
        self.decision=parse_decision(json.dumps(change(scene='gameplay',action={'keys':['num1'],'hold_ms':80,'wait_ms':0})),self.cfg)

    def test_relative_enemy_is_not_removed_from_planner_or_retry_context(self):
        contacts=relative_contacts(self.s)
        self.assertEqual(contacts['player_reference'],[0,0]);self.assertEqual(contacts['contacts'][0]['direction'],'左')
        self.assertEqual(model_data({'relative_contacts':contacts})['relative_contacts']['contacts'][0]['relative_cell'],[-1,0])
        self.assertEqual(context_retry_prompt({'memory':{'relative_contacts':contacts}})['memory']['relative_contacts'],contacts)

    def test_orientation_and_attack_are_separate_fresh_observation_trials(self):
        chosen,trial=self.combat.choose(self.decision,self.s,True)
        self.assertEqual(chosen.keys,['num5','a']);self.assertFalse(trial['orientation_verified'])
        row=receipt_row(observed_keys=chosen.keys,policy_state=self.s,policy_after=self.s)
        evaluation=self.combat.record(trial,row,native(['HP15/15']))
        self.assertEqual(evaluation['status'],'awaiting_attack_trial')
        attack,trial=self.combat.choose(self.decision,self.s,True)
        self.assertEqual(attack.keys,['num1']);self.assertEqual(trial['stage'],'attack_trial')
        hurt=copy.deepcopy(self.s);hurt['metrics']['hp']['values']=[14,15]
        row.update(policy_after=hurt,observed_keys=attack.keys)
        evaluation=self.combat.record(trial,row,native(['HP14/15']))
        self.assertLess(evaluation['reward'],0);self.assertFalse(evaluation['enemy_indicator_disappeared'])
        other,trial=self.combat.choose(self.decision,hurt,True)
        self.assertEqual(trial['stage'],'movement_trial');self.assertNotEqual(other.keys,['num1'])
        self.assertEqual(self.memory.db.execute('SELECT SUM(trials) FROM combat_actions').fetchone()[0],1)

    def test_turn_damage_is_retained_but_changed_target_requires_new_orientation(self):
        _,trial=self.combat.choose(self.decision,self.s,True)
        hurt=copy.deepcopy(self.s);hurt['metrics']['hp']['values']=[14,15]
        self.combat.record(trial,receipt_row(observed_keys=trial['keys'],policy_state=self.s,policy_after=hurt),native(['HP14/15']))
        self.assertAlmostEqual(self.combat.pending['orientation_hp_loss_fraction'],1/15)
        right=copy.deepcopy(self.s);right['minimap']['enemies']=[(1,0)]
        fresh=CombatExperiments(self.memory,self.cfg);_,trial=fresh.choose(self.decision,right,True)
        self.assertEqual(trial['stage'],'orient_trial')
        fresh.record(trial,receipt_row(observed_keys=trial['keys'],policy_state=right,policy_after=right),native(['HP15/15']))
        changed=copy.deepcopy(right);changed['metrics']['floor']['values']=[2]
        self.assertNotEqual(fresh.choose(self.decision,changed,True)[1]['stage'],'attack_trial')

    def test_unknown_dialog_blocked_and_unsent_inputs_are_not_forced(self):
        self.assertIsNone(self.combat.choose(self.decision,self.s,False)[1])
        diagonal=copy.deepcopy(self.s);diagonal['minimap']['enemies']=[(1,1)]
        chosen,trial=self.combat.choose(self.decision,diagonal,True)
        self.assertEqual(chosen.keys,['num5','s','d']);self.assertEqual(trial['stage'],'orient_trial')
        self.decision.scene='dialog';self.assertIsNone(self.combat.choose(self.decision,self.s,True)[1])
        self.decision.scene='gameplay';_,trial=self.combat.choose(self.decision,self.s,True)
        self.assertEqual(self.combat.record(trial,receipt_row(executed=False),{})['status'],'not_updated')

    def test_parser_accepts_only_facing_as_three_key_chord(self):
        value=change(scene='gameplay',action={'keys':['num5','w','d'],'hold_ms':80,'wait_ms':0})
        self.assertEqual(parse_decision(json.dumps(value),self.cfg).keys,['num5','w','d'])
        for invalid in (['w','a','num1'],['num5','w','s'],['num5',{},'d']):
            value['action']['keys']=invalid
            with self.assertRaises(ValueError):parse_decision(json.dumps(value),self.cfg)
        from autoplayer.action_effects import ActionEffects
        self.assertEqual(ActionEffects(self.memory,self.cfg).role(['num5','w','d'],self.s),'orientation_candidate')

    def test_all_four_diagonals_orient_without_moving_then_attack(self):
        for target,directions in (((-1,-1),['w','a']),((1,-1),['w','d']),((-1,1),['s','a']),((1,1),['s','d'])):
            self.combat.pending=None;self.s['minimap']['enemies']=[target]
            chosen,trial=self.combat.choose(self.decision,self.s,True)
            self.assertEqual(chosen.keys,['num5']+directions)
            self.assertEqual(chosen.forecast['first_expected']['player_delta'],[0,0])
            self.combat.record(trial,receipt_row(observed_keys=chosen.keys,policy_state=self.s,policy_after=self.s),native([]))
            self.assertEqual(self.combat.choose(self.decision,self.s,True)[0].keys,['num1'])

    def test_one_gap_footstep_is_single_accepted_trial_then_current_adjacent_attack(self):
        self.s['minimap']['enemies']=[(2,0)]
        chosen,trial=self.combat.choose(self.decision,self.s,True)
        self.assertEqual(chosen.keys,['num6']);self.assertEqual(trial['stage'],'approach_trial')
        self.combat.record(trial,receipt_row(executed=False),{})
        self.assertIsNotNone(self.combat.choose(self.decision,self.s,True)[1])
        row=receipt_row(observed_keys=['num6'],policy_state=self.s,policy_after=self.s)
        self.combat.record(trial,row,native([]))
        self.assertIsNone(self.combat.choose(self.decision,self.s,True)[1])
        adjacent=copy.deepcopy(self.s);adjacent['minimap']['enemies']=[(1,0)]
        self.assertEqual(self.combat.choose(self.decision,adjacent,True)[0].keys,['num5','d'])

    def test_one_gap_does_not_wait_in_dialog_or_low_hp_or_missing_binding(self):
        self.s['minimap']['enemies']=[(2,0)];self.decision.scene='dialog'
        self.assertIsNone(self.combat.choose(self.decision,self.s,True)[1])
        self.decision.scene='gameplay';self.s['metrics']['hp']['values']=[1,15]
        self.assertIsNone(self.combat.choose(self.decision,self.s,True)[1])
        self.s['metrics']['hp']['values']=[15,15]
        self.assertIsNone(self.combat.choose(self.decision,self.s,True,blocked=[['num6']])[1])


class ResultTests(unittest.TestCase):
    def test_result_labels_are_grounded_and_choices_are_excluded(self):
        self.assertEqual(result_screen(DEFEAT),'defeat_result');self.assertEqual(result_screen(SCORES),'high_scores')
        self.assertIsNone(result_screen(native(['今回の冒険の結果'])))
        self.assertIsNone(result_screen(native(['ハイスコア','2:','3:','4:','42G','削除しますか','はい','いいえ'])))

    def test_result_confirmation_avoids_model_confidence_and_uses_fresh_frame(self):
        client=LocalLLM(Settings());client.ocr=SimpleNamespace(read=Mock(return_value=DEFEAT));client._request=Mock(side_effect=AssertionError('Result does not require LLM'))
        decision=client.decide(b'result',[],False)
        self.assertEqual(decision.keys,['num1']);self.assertEqual(decision.confidence,.95)
        self.assertEqual(client.take_state()['decision_policy']['result_kind'],'defeat_result')
        client.context={'unresponsive_actions':[['num1']]};client.ocr.read.return_value=SCORES
        self.assertEqual(client.decide(b'result',[],False).keys,['num2'])
        with self.assertRaises(AssertionError):client.decide(b'different-current-frame',[],False)
        client._request.assert_called_once()

    def test_controller_keeps_tactical_trial_instead_of_overriding_with_movement(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg=Settings(max_steps=2);memory=MemoryStore(Path(folder)/'memory.sqlite',cfg)
            class ReceiptBackend(Backend):
                def act(self,keys,ms,stop):
                    super().act(keys,ms,stop)
                    return {'os_submission':'accepted','events':[{'message':'WM_KEYDOWN'}]}
            backend=ReceiptBackend();backend.observe_turn=Mock(return_value=SimpleNamespace(image=b'current',kind='normal',changed=True,raw_change=0,masked_activity=0,duration_ms=1000))
            script=Script([change(scene='gameplay')]*2);script.ocr=SimpleNamespace(read=Mock(return_value=native(['HP15/15'],b'current')))
            s=state();s['minimap']['enemies']=[(-1,0)]
            try:
                agent=Controller(cfg,backend,script,lambda _:None,Path(folder)/'run.jsonl',memory=memory)
                agent.policy.observe=Mock(return_value=s);agent.run()
                rows=[json.loads(l) for l in agent.journal.read_text().splitlines() if json.loads(l)['event']=='result']
            finally:memory.close()
            self.assertEqual([a[0] for a in backend.actions],[['num5','a'],['num1']])
            self.assertTrue(all(r['policy_choice']['source']=='combat_experiment' for r in rows))
            self.assertEqual(rows[0]['combat_evaluation']['status'],'awaiting_attack_trial')

class MergedRankRegressionTests(unittest.TestCase):
    # OCR text from the submitted 0.13.0 high-score frame, not a game simulator.
    labels=['ハイスコア','OG','12345678','42,783G','24階','23,330G','23階','21,810G','14階','21,716G','21階','19,539G','21階','19,162G','25階','6,838G','13階','2,631G','10階','9:','1,602G','8階','10:','1,094G','11階','ギンギンむら村の','不思議のダンジョンの','地下24階で','爆弾岩に倒される。']
    def test_actual_merged_rank_ocr_and_single_line_layout(self):
        self.assertEqual(result_screen(native(self.labels)),'high_scores')
        self.assertEqual(result_screen(native(['ハイスコア','12345678','42,783G 24階 23,330G 23階 21,810G 14階'])),'high_scores')
    def test_heading_alone_or_gameplay_or_destructive_prompt_is_insufficient(self):
        for labels in (['ハイスコア'],['ハイスコア','12345678','42G','24階'],self.labels+['HP 15/20'],self.labels+['削除しますか','はい','いいえ']):
            self.assertIsNone(result_screen(native(labels)))
    def test_actual_ocr_reaches_confirmation_without_llm_or_fabricated_identity(self):
        from autoplayer.grounding import reconcile
        client=LocalLLM(Settings());ocr=native(self.labels)
        client.ocr=SimpleNamespace(read=Mock(return_value=ocr));client._request=Mock(side_effect=AssertionError('No model call for verified result UI'))
        d=client.decide(b'result',[],False)
        d,r=reconcile(d,{'objects':[]},{},ocr,client.cfg)
        self.assertEqual(d.keys,['num1']);self.assertGreaterEqual(d.confidence,client.cfg.min_confidence)
        client._request.assert_not_called()

class SuppliedBattleMessageTests(unittest.TestCase):
    def test_anonymous_miss_in_supplied_diagnostic_is_not_a_missing_attack_result(self):
        from autoplayer.battle_messages import read_messages
        n=native(['トルネコの攻撃は はずれてしまった。'])
        events=read_messages(n,n['frame_sha256'])
        self.assertEqual(len(events),1);self.assertEqual(events[0]['kind'],'miss')
        self.assertIsNone(events[0]['enemy']);self.assertIsNone(events[0]['damage'])
