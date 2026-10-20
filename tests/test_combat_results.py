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

    def test_damage_during_turn_and_changed_target_do_not_trigger_blind_attack(self):
        _,trial=self.combat.choose(self.decision,self.s,True)
        hurt=copy.deepcopy(self.s);hurt['metrics']['hp']['values']=[14,15]
        self.combat.record(trial,receipt_row(observed_keys=trial['keys'],policy_state=self.s,policy_after=hurt),native(['HP14/15']))
        self.assertIsNone(self.combat.pending)
        right=copy.deepcopy(self.s);right['minimap']['enemies']=[(1,0)]
        fresh=CombatExperiments(self.memory,self.cfg);_,trial=fresh.choose(self.decision,right,True)
        self.assertEqual(trial['stage'],'orient_trial')
        fresh.record(trial,receipt_row(observed_keys=trial['keys'],policy_state=right,policy_after=right),native(['HP15/15']))
        changed=copy.deepcopy(right);changed['metrics']['floor']['values']=[2]
        self.assertNotEqual(fresh.choose(self.decision,changed,True)[1]['stage'],'attack_trial')

    def test_unknown_diagonal_dialog_blocked_and_unsent_inputs_are_not_forced(self):
        self.assertIsNone(self.combat.choose(self.decision,self.s,False)[1])
        diagonal=copy.deepcopy(self.s);diagonal['minimap']['enemies']=[(1,1)]
        self.assertIsNone(self.combat.choose(self.decision,diagonal,True)[1])
        self.decision.scene='dialog';self.assertIsNone(self.combat.choose(self.decision,self.s,True)[1])
        self.decision.scene='gameplay';_,trial=self.combat.choose(self.decision,self.s,True)
        self.assertEqual(self.combat.record(trial,receipt_row(executed=False),{})['status'],'not_updated')


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
