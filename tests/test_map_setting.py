"""Confirmed menu sequence contracts; no game rendering or simulated gameplay."""
import json,threading,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from autoplayer.core import Controller,Settings
from test_policy import state

PNG=b'\x89PNG\r\n\x1a\ncontract'

class MapSettingTests(unittest.TestCase):
    def agent(self,folder,lines):
        agent=Controller.__new__(Controller);agent.cfg=Settings();agent.map_visibility_attempted=False
        agent.stop=Mock();agent.stop.is_set.return_value=False;agent.stop.wait.return_value=False;agent.control=None
        agent.journal=Path(folder)/'run.jsonl'
        agent.backend=SimpleNamespace(capture=Mock(return_value=PNG),act=Mock(return_value={'os_submission':'accepted'}))
        agent.llm=SimpleNamespace(ocr=SimpleNamespace(read=Mock(side_effect=[{'status':'ok','lines':[{'text':t}]} for t in lines])))
        return agent
    def test_confirmed_sequence_once_and_cancel_only_when_menu_remains(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=self.agent(folder,['どうぐ マップ 設定','マップ OFF'])
            agent.hide_map(PNG,state());agent.hide_map(PNG,state())
            self.assertEqual([c.args[0] for c in agent.backend.act.call_args_list],[['num0'],['d'],['num1'],['num2']])
            self.assertTrue(all(not json.loads(line)['learning'] for line in agent.journal.read_text().splitlines()))
    def test_no_detected_overlay_does_not_turn_display_back_on(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=self.agent(folder,[]);hidden=state();hidden['minimap']={'status':'unavailable'}
            self.assertEqual(agent.hide_map(PNG,hidden),PNG);agent.backend.act.assert_not_called()
    def test_missing_menu_confirmation_never_sends_right_and_confirm(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=self.agent(folder,['HP 15/15'])
            agent.hide_map(PNG,state())
            self.assertEqual([c.args[0] for c in agent.backend.act.call_args_list],[['num0']])
    def test_already_closed_menu_does_not_send_world_dash(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=self.agent(folder,['マップ','HP 15/15'])
            agent.hide_map(PNG,state())
            self.assertEqual([c.args[0] for c in agent.backend.act.call_args_list],[['num0'],['d'],['num1']])
    def test_spaced_ocr_labels_are_recognized(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=self.agent(folder,['道 具 マ ッ プ そ の 他','HP 15/15'])
            agent.hide_map(PNG,state())
            self.assertEqual([c.args[0] for c in agent.backend.act.call_args_list],[['num0'],['d'],['num1']])
    def test_menu_state_never_requests_num7_map_observation(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=self.agent(folder,[]);agent.policy=object()
            agent.backend.capture_map_display=Mock()
            current=state();current['menu_evidence']=True
            self.assertIsNone(agent.map_sample(PNG,current,'map_observation',True))
            agent.backend.capture_map_display.assert_not_called()
    def test_pause_stops_remaining_sequence(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=self.agent(folder,[]);agent.stop.wait.return_value=True
            agent.hide_map(PNG,state())
            self.assertEqual([c.args[0] for c in agent.backend.act.call_args_list],[['num0']])
