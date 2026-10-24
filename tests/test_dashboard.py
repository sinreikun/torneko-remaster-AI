"""UI model, widget contracts and intervention handoff; no simulated game."""
import tempfile,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from autoplayer.dashboard import LearningTraces,replace_text,append_text
from autoplayer.telemetry import RecognitionState
from autoplayer.session import SessionControl
from autoplayer.console_play import ConsoleCommands
from autoplayer.core import Settings
from autoplayer.ui import Application
from autoplayer.world_model import WorldModel
from autoplayer.memory import MemoryStore

class DashboardTests(unittest.TestCase):
    def test_graphs_are_bounded_and_use_observed_zero_without_inventing_missing_values(self):
        traces=LearningTraces()
        for _ in range(245):traces.update({'event':'result','confidence':.5,'policy_learning':{'reward':0}})
        self.assertEqual(len(traces.samples),240);self.assertEqual(traces.turn,245)
        self.assertEqual(traces.samples[-1]['reward'],0);self.assertIsNone(traces.samples[-1]['q'])
        traces.update({'event':'decision'});self.assertEqual(traces.turn,245)
    def test_missing_samples_break_lines_and_series_can_be_selected_or_combined(self):
        traces=LearningTraces()
        for value in (1,2,None,4,5):traces.update({'event':'result','policy_learning':{'reward':value}})
        canvas=Mock();traces.paint(canvas,{'reward':True},(0,0,280,170))
        self.assertEqual(canvas.create_line.call_count,2)
        canvas=Mock();traces.paint(canvas,{},(0,0,280,170));canvas.create_line.assert_not_called()
    def test_menu_keeps_map_and_status_as_last_observation_but_not_hp(self):
        state=RecognitionState();m={'status':'observed','grid_size':[54,32],'player_cell':[2,3],'accumulated_cells':[[2,3]]}
        state.update({'event':'perception','policy_state':{'minimap':m,'metrics':{'hp':{'values':[15,15]},'strength':{'values':[8,8]},'sword':{'values':[0]},'shield':{'values':[0]},'experience':{'values':[2]}}}})
        state.update({'event':'result','policy_after':{'minimap':{'status':'unavailable'},'metrics':{}}})
        self.assertEqual(state.grid()[(2,3)],'player');self.assertTrue(state.map['retained'])
        self.assertEqual(state.metrics['strength']['values'],[8,8]);self.assertTrue(state.metrics['strength']['retained']);self.assertNotIn('hp',state.metrics)
        self.assertIn('未再確認',state.summary())
        state.update({'event':'perception','policy_state':{'minimap':m,'metrics':{'strength':{'values':[7,8]}}}})
        self.assertNotIn('retained',state.metrics['strength']);self.assertEqual(state.metrics['strength']['values'],[7,8])
        state.update({'event':'disconnected'});self.assertEqual(state.metrics,{});self.assertEqual(state.grid(),{})
    def test_floor_change_resets_map_but_keeps_equipment_until_reobserved(self):
        s=RecognitionState();s.metrics={'strength':{'values':[8,8]},'floor':{'values':[1]}};s.map={'status':'observed','grid_size':[54,32]}
        s.update({'event':'perception','policy_state':{'floor_confirmation':{'changed':True},'minimap':{},'metrics':{'floor':{'values':[2]}}}})
        self.assertEqual(s.map,{});self.assertEqual(s.metrics['strength']['values'],[8,8])
        s.update({'event':'perception','world_observation':{'new_adventure':True},'policy_state':{'metrics':{},'minimap':{}}})
        self.assertEqual(s.metrics,{})
    def test_replacement_and_append_keep_top_line_and_horizontal_offset(self):
        widget=Mock();widget.index.side_effect=lambda index:'40.0' if index=='@0,0' else '120.0';widget.xview.return_value=(.2,.8)
        replace_text(widget,'new text');widget.yview.assert_called_with('40.0');widget.xview_moveto.assert_called_with(.2);widget.see.assert_not_called()
        append_text(widget,'more');widget.yview.assert_called_with('40.0');widget.see.assert_not_called()
    def test_console_button_interrupts_inference_and_can_intervene_while_auto_paused(self):
        a=Application.__new__(Application);a.cfg=Settings();a.stop=threading.Event();a.control=SessionControl(a.stop);a.control.paused.set()
        a.interventions=ConsoleCommands(a.cfg);a.interventions.set_ready(True);a.busy=lambda:True;a.recording_human=False
        a.console_hold=SimpleNamespace(get=lambda:80);a.console_wait=SimpleNamespace(get=lambda:100);a.console_status=Mock();a.console_label=Mock()
        a.console_send(['w'])
        self.assertTrue(a.control.intervention.is_set());self.assertTrue(a.control.is_set());self.assertTrue(a.control.paused.is_set());self.assertTrue(a.control.checkpoint())
        self.assertEqual(a.interventions.queue.get_nowait()['keys'],['w'])
        self.assertFalse(a.interventions.ready)
    def test_retained_internal_status_is_separate_from_current_metrics(self):
        with tempfile.TemporaryDirectory() as t:
            memory=MemoryStore(Path(t)/'memory.sqlite',Settings())
            try:
                w=WorldModel(memory);w.observe(b'current',{'status':'unavailable','lines':[]},{'metrics':{'strength':{'values':[8,8]}},'minimap':{}})
                w.observe(b'next',{'status':'unavailable','lines':[]},{'metrics':{},'minimap':{}})
                self.assertNotIn('strength',w.context()['metrics'])
                self.assertEqual(w.context()['last_known_metrics']['strength']['values'],[8,8])
                self.assertEqual(WorldModel(memory).last_known_metrics['strength']['status'],'last_observed_not_current_confirmation')
            finally:memory.close()

    def test_closing_intervention_window_does_not_stop_automatic_play(self):
        a=Application.__new__(Application);a.console_active=False;a.console_commands=Mock();a.console_commands.closed=False
        a.console_window=Mock();window=a.console_window;a.request_stop=Mock()
        a.close_console();a.request_stop.assert_not_called();window.destroy.assert_called_once();self.assertIsNone(a.console_window)

    def test_top_actions_wrap_in_order_and_fit_each_window_width(self):
        from autoplayer.dashboard import flow_positions
        sizes=[(100,28),(130,28),(90,30),(180,28)]
        for width in (180,340,640,1000):
            positions,height=flow_positions(width,sizes)
            self.assertEqual(len(positions),len(sizes))
            for x,y,w,h in positions:self.assertGreaterEqual(x,8);self.assertLessEqual(x+w,width-8);self.assertLessEqual(y+h,height)
            self.assertEqual([p[1] for p in positions],sorted(p[1] for p in positions))
        self.assertGreater(flow_positions(180,sizes)[1],flow_positions(1000,sizes)[1])
