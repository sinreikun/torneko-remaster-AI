import copy,json,unittest
from autoplayer.nextgen.supervisor import compact_prompt
from autoplayer.performance import bounded_request_prompt,context_retry_prompt
from autoplayer.nextgen.ui_vision import UIVision
from test_spatial_ui import measured

class ContextLimits(unittest.TestCase):
    def test_nested_evidence_is_bounded_and_retry_is_smaller(self):
        prompt={'task':'world_model_supervision','goal':'現在の選択を確認する','operator_guidance':{'text':'慎重に'},'candidates':[{'id':'A1','keys':['s']}],
          'current':{'quotes':['はい','いいえ'],'objects':[],'spatial_ui':{'elements':[],'interaction':{'history':['観測'*500]*30}}},
          'goal_hierarchy':{'current_goal':{'label':'選択を確認'},'evidence':['証拠'*1000]*20},'recent_prediction_checks':[]}
        original=copy.deepcopy(prompt)
        first=bounded_request_prompt(prompt);retry=context_retry_prompt(first)
        length=lambda p:len(json.dumps(p,ensure_ascii=False,separators=(',',':')))
        self.assertLessEqual(length(first),6000);self.assertLessEqual(length(retry),3500);self.assertLess(length(retry),length(first))
        for key in ('task','goal','operator_guidance','candidates'):self.assertEqual(retry[key],prompt[key])
        self.assertEqual(prompt,original)
    def test_repeated_identical_frame_does_not_duplicate_tracks(self):
        v=UIVision();a,_=measured(v,0);b,png=measured(v,1)
        native={'captured_at':1,'lines':[]}
        # Reuse the same OCR boxes from the primitive fixture.
        from test_spatial_ui import sample
        png,native=sample(1)
        for _ in range(8):
            ui=v.measure(png,native)
            self.assertEqual(len(ui['candidates']),len({c['id'] for c in ui['candidates']}))
