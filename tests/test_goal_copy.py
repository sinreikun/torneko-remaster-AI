"""Regression for restated mission text; no game or model simulation."""
import json,unittest
from autoplayer.core import Settings,parse_decision,response_quality_issue
from test_agent import change

class GoalCopyTests(unittest.TestCase):
    def decision(self,reason):return parse_decision(json.dumps(change(reason=reason)),Settings())
    def test_changed_ending_does_not_hide_a_long_goal_copy(self):
        goal=Settings().goal
        reason=goal.rsplit('。',2)[0]+'。階に到達したことや結果画面だけでは完了とはしない。'
        self.assertTrue(response_quality_issue(self.decision(reason),goal_text=goal))
    def test_short_goal_reference_with_concrete_action_reason_is_allowed(self):
        reason='箱を持ち帰る目標に向け、現在の右側に通路の候補が見えるので右入力を短く試す。直前の左入力では変化を確認できなかったため今回は避け、次の観測で通路との距離や画面の移り方を比較する。'
        self.assertFalse(response_quality_issue(self.decision(reason),goal_text=Settings().goal))
    def test_copy_check_does_not_reject_an_unrelated_long_reason(self):
        reason='現在のメニューには道具とその他の項目があるが、今の小目標で必要な持ち物の操作は確認できていない。いったん取消入力を送り、背後の通行候補を現在の画像から読み直してから別の方向を検討する。'
        self.assertFalse(response_quality_issue(self.decision(reason),goal_text=Settings().goal))
