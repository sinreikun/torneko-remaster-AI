"""Presentation of recorded facts, without a game or an LLM environment."""
import unittest
from unittest.mock import Mock
from types import SimpleNamespace
from autoplayer.observer_view import ObserverPanels,ObserverDigest,SECTIONS
from autoplayer.ui import Application

class ObserverViewTests(unittest.TestCase):
    def test_all_old_details_are_accessible_and_alias_updates_keep_other_sources(self):
        panels=ObserverPanels()
        panels.put('仮説','対象未確定');panels.put('修正履歴','旧推測撤回');panels.put('新発見','画面観測')
        text=panels.render('学習・仮説')
        for phrase in ['対象未確定','旧推測撤回','画面観測']:self.assertIn(phrase,text)
        self.assertTrue({'結果','目的','運用計画','選択行動と理由','行動学習','候補と評価','現在状態','バックエンド状態','キー配置・ミニマップ'}<=set(sum(SECTIONS.values(),())))
    def test_values_replace_instead_of_accumulating_and_render_is_bounded(self):
        panels=ObserverPanels()
        for i in range(100):panels.put('目的',str(i))
        self.assertEqual(panels.values['目的'],{'目的':'99'})
        for title in SECTIONS['学習・仮説']:panels.put(title,'x'*50000)
        self.assertLessEqual(len(panels.render('学習・仮説')),24000)
    def test_input_plan_is_not_an_observed_result_and_result_not_goal_success(self):
        digest=ObserverDigest()
        digest.update({'event':'decision','step':7,'keys':['num1'],'subgoal':'識別対象を選ぶ','reason':'現在行を確認','forecast':{'first_expected':{'interface':'target'}}})
        self.assertIn('次の入力',digest.summary());self.assertIn('結果の観測待ち',digest.summary())
        digest.update({'event':'result','step':7,'observed_keys':['num1'],'executed':True,'changed':True,'operation_evaluation':{'progress':True}})
        self.assertIn('直近の入力',digest.summary());self.assertNotIn('次の入力',digest.summary())
        self.assertIn('未検証',digest.summary());self.assertIn('目標達成とは別',digest.summary())
        self.assertIn('target',digest.journal())
    def test_history_is_bounded_and_does_not_attach_other_turn_prediction(self):
        digest=ObserverDigest();digest.update({'event':'decision','step':1,'forecast':{'first_expected':{'hp':42}}})
        for i in range(2,15):digest.update({'event':'result','step':i})
        self.assertEqual(len(digest.history),6);self.assertNotIn('42',digest.journal());self.assertIn('表示未取得',digest.journal())
    def test_hidden_panel_update_does_not_redraw_selected_section(self):
        app=Application.__new__(Application);app.observer_panels=ObserverPanels();app.observer_section=SimpleNamespace(get=lambda:'予測と結果');app.refresh_observer_section=Mock()
        app.set_panel('バックエンド状態',{'capture':'PrintWindow'});app.refresh_observer_section.assert_not_called()
        app.set_panel('結果',{'changed':False});app.refresh_observer_section.assert_called_once()
        self.assertIn('PrintWindow',app.observer_panels.render('接続・操作'))
    def test_switch_restores_each_sections_reading_position(self):
        app=Application.__new__(Application);app.detail_body=Mock();app.detail_body.index.return_value='8.0';app.detail_body.xview.return_value=(.1,1)
        app.observer_shown_section='予測と結果';app.observer_section=SimpleNamespace(get=lambda:'目的・手順');app.observer_positions={'目的・手順':('4.0',0.)};app.ui_state={};app.save_ui=Mock();app.refresh_observer_section=Mock()
        app.change_observer_section()
        self.assertEqual(app.observer_positions['予測と結果'],('8.0',.1));app.detail_body.yview.assert_called_with('4.0');self.assertEqual(app.ui_state['observer_section'],'目的・手順')
