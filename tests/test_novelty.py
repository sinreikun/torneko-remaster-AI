"""SQLite/OCR novelty contracts, without rendered games or live input."""
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from autoplayer.core import Settings
from autoplayer.memory import MemoryStore
from autoplayer.novelty import NoveltyExplorer
from test_policy import receipt_row,state


def ocr(*labels):return {'status':'ok','lines':[{'text':s} for s in labels]}


class NoveltyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.cfg=Settings();self.memory=MemoryStore(Path(self.temp.name)/'memory.sqlite',self.cfg)
        self.addCleanup(self.memory.close);self.explorer=NoveltyExplorer(self.memory,self.cfg)
        self.menu=ocr('道具','3/20','詳細表示切替','整理')

    def test_untried_candidates_precede_old_action_and_survive_reopening(self):
        from autoplayer.core import parse_decision
        from test_agent import change
        import json
        decision=parse_decision(json.dumps(change(action={'keys':[],'hold_ms':1,'wait_ms':0})),self.cfg)
        chosen,info=self.explorer.choose_menu(decision,self.menu)
        first=chosen.keys[0]
        result=self.explorer.record(receipt_row(first),self.menu,self.menu)
        self.assertLess(result['reward'],0)
        reopened=NoveltyExplorer(self.memory,self.cfg)
        chosen,info=reopened.choose_menu(decision,self.menu)
        self.assertNotEqual(chosen.keys,[first]);self.assertEqual(info['selected']['trials'],0)
        blocked=[[k] for k in ('w','a','s','d','num0','num2')]
        self.assertIsNone(reopened.choose_menu(decision,self.menu,blocked)[1])

    def test_repeated_transition_is_not_a_repeated_discovery(self):
        after=ocr('道具','マップ','足元','その他')
        first=self.explorer.record(receipt_row('num2'),self.menu,after)
        repeated=self.explorer.record(receipt_row('num2'),self.menu,after)
        self.assertTrue(first['new_state']);self.assertGreater(first['reward'],0)
        self.assertFalse(repeated['new_state']);self.assertFalse(repeated['new_outcome'])
        self.assertLess(repeated['reward'],0)

    def test_natural_change_and_unsent_action_do_not_establish_transition(self):
        after=ocr('道具','マップ','足元','その他')
        result=self.explorer.record(receipt_row('w',neutral_before={'raw_change':.2}),self.menu,after)
        self.assertEqual(result['reward'],0);self.assertFalse(result['new_state'])
        self.assertEqual(self.memory.db.execute('SELECT COUNT(*) FROM novelty_outcomes').fetchone()[0],0)
        for row in (receipt_row('w',executed=False),receipt_row('w',dry_run=True),receipt_row('w',turn_kind='aborted')):
            self.assertEqual(self.explorer.record(row,self.menu,after)['status'],'not_updated')

    def test_counter_and_item_ocr_flicker_do_not_create_new_menu(self):
        second=ocr('道 具','4 / 20','詳 細 表 示 切 替','新 整 理','OCRの揺れ','満腹度80%')
        self.assertEqual(self.explorer.state(self.menu),self.explorer.state(second))
        a=state();b=state('different hash');b['minimap']['shape']='changed'
        self.assertEqual(self.explorer.state(self.menu,a),self.explorer.state(self.menu,b))

    def test_unknown_and_destructive_menu_never_get_guessed_confirm(self):
        decision=SimpleNamespace(status='continue',risk='normal',keys=[])
        for n in (ocr('何かの説明'),ocr('道具','マップ','足元','その他','削除')):
            self.assertIsNone(self.explorer.choose_menu(decision,n)[1])

    def test_tried_ineffective_actions_do_not_outrank_frequent_observed_transition(self):
        from autoplayer.core import parse_decision
        from test_agent import change
        import json
        for key in ('w','a','s','d','num0'):
            self.explorer.record(receipt_row(key),self.menu,self.menu)
        after=ocr('道具','マップ','足元','その他')
        for _ in range(12):self.explorer.record(receipt_row('num2'),self.menu,after)
        decision=parse_decision(json.dumps(change(action={'keys':[],'hold_ms':1,'wait_ms':0})),self.cfg)
        selected,detail=self.explorer.choose_menu(decision,self.menu)
        self.assertEqual(selected.keys,['num2'])
        self.assertEqual(detail['selected']['trials'],12)
        self.assertEqual(detail['selected']['observed_state_change_rate'],1)

    def test_repeated_menu_opening_is_reconsidered_but_first_discovery_is_not(self):
        decision=SimpleNamespace(keys=['num0'],status='continue',risk='normal')
        current=state();row=receipt_row('num0',policy_state=current)
        self.explorer.record(row,self.menu,self.menu)
        self.assertIsNone(self.explorer.repeated_menu_opening(self.menu,current,decision))
        self.explorer.record(row,self.menu,self.menu)
        self.assertIsNotNone(self.explorer.repeated_menu_opening(self.menu,current,decision))
        decision.keys=['num1']
        self.assertIsNone(self.explorer.repeated_menu_opening(self.menu,current,decision))

    def test_controller_replaces_repeated_menu_opening_with_fresh_map_exploration(self):
        from unittest.mock import Mock
        from autoplayer.core import Controller
        from test_agent import Backend,Script,change
        import json
        class ReceiptBackend(Backend):
            def act(self,keys,ms,stop):
                super().act(keys,ms,stop)
                return {'os_submission':'accepted','events':[{'message':'WM_KEYDOWN'}]}
        backend=ReceiptBackend()
        backend.observe_turn=Mock(return_value=SimpleNamespace(image=b'current',kind='no_change',changed=False,raw_change=0,masked_activity=0,duration_ms=1000))
        script=Script([change(action={'keys':['num0'],'hold_ms':80,'wait_ms':0},confidence=.9)]*3)
        script.ocr=SimpleNamespace(read=Mock(return_value=ocr('1階','HP15/15')))
        agent=Controller(Settings(max_steps=3),backend,script,lambda _:None,Path(self.temp.name)/'run.jsonl',memory=self.memory)
        agent.policy.observe=Mock(return_value=state());agent.run()
        rows=[json.loads(l) for l in agent.journal.read_text().splitlines() if json.loads(l)['event']=='result']
        self.assertNotEqual(backend.actions[0][0],backend.actions[1][0])
        self.assertIn(backend.actions[-1][0][0],'wasd')
        self.assertEqual(rows[-1]['active_input']['source'],'information_collection')
        self.assertEqual(rows[-1]['llm_proposal']['keys'],['num0'])
