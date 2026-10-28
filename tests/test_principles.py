"""User objective/prompt and saved-profile contracts, not gameplay simulation."""
import io,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from autoplayer.core import LocalLLM,Settings
from autoplayer.principles import LEGACY_GOAL,TORNEKO_GOAL,action_principles
from autoplayer.profiles import ProfileStore
from test_agent import OBSERVED,change

class PrincipleTests(unittest.TestCase):
    def test_standard_goal_migrates_but_custom_and_other_game_goals_do_not(self):
        self.assertEqual(Settings(goal=LEGACY_GOAL).validate().goal,TORNEKO_GOAL)
        self.assertEqual(Settings(goal='1階の扉を調べる').validate().goal,'1階の扉を調べる')
        self.assertEqual(Settings(window_title='Other game').validate().goal,LEGACY_GOAL)
        self.assertNotIn('box_retrieval',action_principles(False))
    def test_saved_legacy_profile_is_migrated_on_selection(self):
        with tempfile.TemporaryDirectory() as t:
            store=ProfileStore(t);target={'title':'トルネコ','executable':'C:/game.exe','class_name':'Game'}
            store.save(target,Settings(goal=LEGACY_GOAL))
            cfg=store.select(target,Settings())
            self.assertEqual(cfg.goal,TORNEKO_GOAL)
            cfg.goal='出口を探す';store.save(target,cfg)
            self.assertEqual(store.select(target,Settings()).goal,'出口を探す')
    def test_goal_floors_are_targets_and_names_are_not_invented(self):
        p=action_principles(True);box=p['box_retrieval']
        self.assertEqual(box['target_floor_candidates'],[10,27,99]);self.assertIsNone(box['target_name'])
        self.assertTrue(p['not_current_observation']);self.assertIn('上り階段',box['after_acquisition'])
        self.assertIn('脱出',box['success']);self.assertIn('持ち帰り',box['success'])
        self.assertIn('同時回収する前提を置かない',box['attempt_scope'])
        self.assertTrue(any('因果仮説' in s for s in p['experience_cycle']))
    def test_planner_gets_principles_even_when_its_request_is_overridden(self):
        client=LocalLLM(Settings());client.ocr.read=Mock(return_value={'status':'unavailable','lines':[]})
        client.observe_scene=Mock(return_value=OBSERVED);client._request=Mock(return_value=json.dumps(change()))
        client.decide(b'captured-image',[],False)
        prompt=client._request.call_args.args[2]
        self.assertEqual(prompt['goal'],TORNEKO_GOAL)
        self.assertEqual(prompt['action_principles']['box_retrieval']['target_floor_candidates'],[10,27,99])
    def test_every_plan_repair_review_keeps_principles_after_compaction_but_observer_is_independent(self):
        requests=[]
        def response(request,timeout):
            requests.append(json.loads(request.data));return io.BytesIO(b'{"message":{"content":"{}"}}')
        client=LocalLLM(Settings());client.context_compaction=True
        with patch('urllib.request.build_opener',return_value=SimpleNamespace(open=response)):
            for task in ('decision','decision_protocol_repair','self_improvement_review','scene_observation'):
                client._request(None,'日本語',{'task':task,'goal':TORNEKO_GOAL,'recent_history':[{'observation':'以前'*10000}],'operator_guidance':{'revision':3,'text':'慎重に'}})
        for i,r in enumerate(requests):
            prompt=json.loads(r['messages'][1]['content'])
            self.assertEqual(prompt['operator_guidance']['text'],'慎重に')
            if i<3:self.assertEqual(prompt['action_principles'],action_principles(True))
            else:self.assertNotIn('action_principles',prompt)
    def test_completion_review_gets_acquisition_and_return_conditions(self):
        client=LocalLLM(Settings());client.ocr.read=Mock(return_value={'status':'unavailable','lines':[]})
        facts={'scene_type':'victory','visible_text':['持ち帰った'],'description':'地上に戻った表示'}
        answer={'achieved':False,'confidence':.9,'evidence':['持ち帰った'],'reason':'対象箱を未確認','remaining_actions':['対象箱を確認する']}
        client._request=Mock(side_effect=[json.dumps(facts),json.dumps(answer)])
        self.assertFalse(client.verify_completion(b'captured-image')['achieved'])
        first,second=client._request.call_args_list
        self.assertNotIn('completion_principles',first.args[2])
        self.assertEqual(second.args[2]['completion_principles'],action_principles(True)['box_retrieval'])

class OrderedProtocolTests(unittest.TestCase):
    def test_strategy_order_comes_from_the_shared_protocol(self):
        from autoplayer.decision_protocol import STRATEGY_ORDER,EXECUTION_ORDER,SUPERVISION_INSTRUCTION
        from autoplayer.principles import action_principles
        self.assertEqual(action_principles(True)['priority_order'],list(STRATEGY_ORDER))
        self.assertLess(EXECUTION_ORDER.index('urgent_recovery'),EXECUTION_ORDER.index('continue_scoped_operation'))
        self.assertIn('主メニューで道具→一覧で目的の品の行→使用方法で目的の操作名→決定→結果確認',SUPERVISION_INSTRUCTION)
