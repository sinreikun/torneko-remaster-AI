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
