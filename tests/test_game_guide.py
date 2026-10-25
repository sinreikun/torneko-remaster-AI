"""Curated source/edition/control and planner injection contracts; no game play."""
import json,unittest
from unittest.mock import Mock
from types import SimpleNamespace
from autoplayer.game_guide import GameGuide
from autoplayer.core import Settings,LocalLLM
from test_agent import change

class GameGuideTests(unittest.TestCase):
    def test_adjacent_contact_receives_action_preconditions_not_enemy_identity(self):
        guide=GameGuide(Settings());ctx=guide.context({'status':'ok','lines':[]},'gameplay',{'relative_contacts':{'contacts':[{'adjacent':True}]}},{'floor':{'values':[1]},'hp':{'values':[9,15]}})
        self.assertIn('adjacent-attack',ctx['selected_ids']);self.assertEqual(ctx['current_keys']['attack'],['num1']);self.assertEqual(ctx['current_keys']['face'],['num5'])
        self.assertLessEqual(len(ctx['rules']),4);self.assertIn('仮説',ctx['instruction'])
    def test_low_hp_and_food_references_remain_cross_edition(self):
        ctx=GameGuide(Settings()).context({},'gameplay',{}, {'hp':{'values':[3,15]},'fullness':{'values':[0]},'floor':{'values':[1]}})
        selected={r['id']:r for r in ctx['rules']}
        self.assertEqual(selected['recovery']['applicability'],'sfc_reference');self.assertIn('food',selected)
        self.assertFalse(any(s['kind']=='publisher_store' and s['edition']=='sfc' for s in ctx['sources']))
    def test_keys_come_from_current_allowed_bindings(self):
        cfg=Settings(allowed_keys=['a','d'],key_bindings={'a':'方向転換','d':'決定／攻撃'})
        ctx=GameGuide(cfg).context({},'gameplay',{'relative_contacts':{'contacts':[{'adjacent':True}]}},{})
        self.assertEqual(ctx['current_keys']['face'],['a']);self.assertEqual(ctx['current_keys']['attack'],['d'])
        self.assertFalse(any(k in ('enter','space','num1','num5') for keys in ctx['current_keys'].values() for k in keys))
    def test_other_game_and_no_current_evidence_do_not_receive_combat_assumptions(self):
        self.assertEqual(GameGuide(Settings(window_title='Other')).context({},'gameplay',{},{}),{'status':'not_applicable'})
        self.assertNotIn('adjacent-attack',GameGuide(Settings()).context({},'unknown',{}, {})['selected_ids'])
    def test_outside_choice_and_death_are_separate_contexts(self):
        g=GameGuide(Settings());outside=g.context({},'gameplay',{'exterior_action_policy':{'mode':'exit_exploration'}},{})
        self.assertEqual(outside['selected_ids'],['outside-exit'])
        native={'status':'ok','lines':[{'text':t} for t in ('やりなおしますか？','はい','いいえ')]}
        c=g.context(native,'dialog',{},{});self.assertIn('death-retry',c['selected_ids']);self.assertIn('dialogue',c['selected_ids'])
    def test_named_items_retrieve_their_distinct_use_methods(self):
        native={'status':'ok','lines':[{'text':t} for t in ('道具','読む','飲む','投げる','聖域の巻物','ラリホー草')]}
        guide=GameGuide(Settings()).context(native,'inventory',{}, {})
        rules={r['id']:r for r in guide['rules']}
        self.assertIn('use-sanctuary',rules);self.assertIn('置いて',rules['use-sanctuary']['expected_effect'])
        self.assertIn('use-sleep',rules);self.assertIn('自分が眠る',rules['use-sleep']['expected_effect'])
        self.assertTrue(rules['use-sleep']['procedure']);self.assertEqual(rules['use-sleep']['applicability'],'sfc_reference')
    def test_urgent_hp_is_not_displaced_by_inventory_names(self):
        native={'status':'ok','lines':[{'text':t} for t in ('道具','薬草','ルーラ草','火炎草','イオの巻物','聖域の巻物','バイキルトの巻物')]}
        guide=GameGuide(Settings()).context(native,'inventory',{}, {'hp':{'values':[1,15]}})
        self.assertIn('recovery',guide['selected_ids']);self.assertLessEqual(len(guide['rules']),4)
    def test_enemy_hypotheses_require_names_as_well_as_contact(self):
        g=GameGuide(Settings());contact={'relative_contacts':{'contacts':[{'adjacent':True}]}}
        self.assertNotIn('enemy-bomb',g.context({},'gameplay',contact,{})['selected_ids'])
        named=g.context({'status':'ok','lines':[{'text':'爆弾岩'}]},'gameplay',contact,{})
        self.assertIn('enemy-bomb',named['selected_ids']);self.assertEqual(next(r for r in named['rules'] if r['id']=='enemy-bomb')['applicability'],'sfc_reference')
    def test_planner_receives_curated_guide_and_records_selected_ids(self):
        import hashlib
        cfg=Settings();client=LocalLLM(cfg);png=b'planner-protocol';native={'status':'ok','frame_sha256':hashlib.sha256(png).hexdigest(),'lines':[{'text':'1階 HP3/15 満腹度10%'}]}
        client.ocr=SimpleNamespace(read=Mock(return_value=native));client.observe_scene=Mock(return_value={'scene_type':'gameplay','description':'現在の画面。','visible_text':['HP3/15'],'confidence':.9})
        client._request=Mock(return_value=json.dumps(change(scene='gameplay',action={'keys':['w'],'hold_ms':80,'wait_ms':0},observation='現在の画面にHP3/15。',reason='上方向への入力を1回試す。',goal_success='次の画面で移動結果を比較する。')))
        client.decide(png,[],False)
        guide=client._request.call_args.args[2]['game_guide'];self.assertIn('recovery',guide['selected_ids'])
        self.assertEqual(client.take_state()['game_guide']['catalog_sha256'],guide['catalog_sha256'])
    def test_user_combat_basics_reach_model_with_source_and_uncertainty(self):
        g=GameGuide(Settings());c=g.context({},'gameplay',{'relative_contacts':{'contacts':[{'adjacent':True}]}},{'floor':{'values':[1]},'hp':{'values':[15,15]}})
        f={r['id']:r for r in c['foundations']}
        self.assertIn('enemy-threat-policy',f);self.assertIn('status-combat-model',f)
        attack=f['attack-methods-and-accuracy']
        self.assertIn('1/8',attack['expected_effect']);self.assertIn('必ず命中',attack['expected_effect']);self.assertIn('固定周期',attack['verify_or_revise'])
        self.assertIn('うつ',attack['expected_effect']);self.assertIn('装備ターン',attack['expected_effect'])
        self.assertEqual(attack['applicability'],'user_environment')
        self.assertTrue(any(s['id']=='user-mechanics-20261026' and s['kind']=='user_supplied' for s in c['sources']))
    def test_user_inventory_knowledge_includes_full_hp_and_equipment_rules(self):
        c=GameGuide(Settings()).context({'status':'ok','lines':[{'text':t} for t in ('道具','薬草','弟切草','装備','うつ')]},'inventory',{}, {})
        f={r['id']:r for r in c['foundations']}
        self.assertIn('20個',f['item-value-policy']['expected_effect']);self.assertIn('交換',f['item-value-policy']['expected_effect'])
        self.assertIn('薬草を使用すると最大HPが1、弟切草なら2',f['full-hp-herbs']['expected_effect'])
        self.assertIn('各1つ',f['inventory-description-equipment']['expected_effect'])
        self.assertEqual(c['current_keys']['item_details'],['num7'])
        self.assertIn('通常場面',f['inventory-description-equipment']['verify_or_revise'])
    def test_description_role_uses_current_binding_and_is_not_outside_exploration(self):
        cfg=Settings(allowed_keys=['a','d'],key_bindings={'a':'品の詳細表示','d':'決定／攻撃'})
        g=GameGuide(cfg);n={'status':'ok','lines':[{'text':'道具 装備'}]}
        self.assertEqual(g.context(n,'inventory',{}, {})['current_keys']['item_details'],['a'])
        outside=g.context({},'gameplay',{'exterior_action_policy':{'mode':'exit_exploration'}},{})
        self.assertEqual(outside['foundations'],[])
