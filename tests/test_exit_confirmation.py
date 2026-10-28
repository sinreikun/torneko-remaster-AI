"""Regression for the actual OCR exit sentence in the submitted diagnostic."""
import json,unittest
from autoplayer.core import Settings,parse_decision
from autoplayer.active_input import ActiveInput,DANGEROUS
from autoplayer.information_exploration import InformationExploration
from autoplayer.interface_responses import layout_group
from test_agent import change

class ExitConfirmationTests(unittest.TestCase):
    def native(self,text):return {'status':'ok','lines':[{'text':text,'confidence':.99,'words':[{'box':[230,502,700,38]}]},{'text':'いいえ','confidence':.99,'words':[{'box':[900,450,60,30]}]},{'text':'はい','confidence':.99,'words':[{'box':[900,400,60,30]}]}]}
    def test_submitted_exit_sentence_cancels_despite_fresh_llm_confirmation(self):
        native=self.native('ぼうけんを終了します。よろしいですか？');cfg=Settings()
        decision=parse_decision(json.dumps(change(scene='dialog',confidence=.9)),cfg)
        active=ActiveInput(cfg)
        selected,report=active.select(decision,native,preserve_proposal=True)
        self.assertEqual(selected.keys,['num2']);self.assertIn('終了・中断',selected.reason)
        selected,coverage=InformationExploration(cfg).choose(selected,native,{})
        self.assertEqual(selected.keys,['num2']);self.assertNotIn('selected',coverage)
    def test_sentence_variants_and_line_breaks_share_the_guard(self):
        for text in ['ぼうけんを終了します。\nよろしいですか？','冒険を中断してもいいですか？','冒険をやめます。よろしいですか？','終了しますか？']:
            with self.subTest(text=text):self.assertIsNotNone(DANGEROUS.search(text))
    def test_menu_labels_and_ordinary_advice_are_not_exit_confirmation(self):
        for text in ['道具\nプレイ記録\nせってい\nその他\n中断','王様「もう一度\nわしのアドバイスを\n聞くか？','話を終了した後で冒険を再開します。']:
            with self.subTest(text=text):self.assertIsNone(DANGEROUS.search(text))
    def test_protected_sentence_never_becomes_transfer_layout(self):
        self.assertIsNone(layout_group(self.native('ぼうけんを終了します。よろしいですか？'),[1280,720],{}))
