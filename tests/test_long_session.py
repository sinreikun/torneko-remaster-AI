"""Recorded cursor/request regressions and presentation bounds; no game simulation."""
import copy,json,queue,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from autoplayer.world_model import Inventory
from autoplayer.ui_budget import UIEventQueue
from autoplayer.dashboard import dashboard_sashes,LearningTraces,append_text
from autoplayer.core import LocalLLM,Settings
from autoplayer.supervision import StateLedger
from autoplayer.performance import bounded_request_prompt,context_retry_prompt

FIXTURES=Path(__file__).parent/'fixtures'
class LongSessionTests(unittest.TestCase):
    def test_real_first_row_cursor_is_22_pixels_high_against_41_pixel_ocr_box(self):
        native=json.loads((FIXTURES/'inventory-cursor-1793137568903748700.json').read_text())
        png=(FIXTURES/'inventory-cursor-1793137568903748700.png').read_bytes()
        self.assertEqual(Inventory.cursor_index(native,png),0)
        self.assertIsNone(Inventory.cursor_index(native))
    def test_thousands_of_ui_events_remain_bounded_and_control_messages_survive(self):
        q=UIEventQueue(limit=24);q.put(('connected',object()))
        for i in range(2000):
            q.put(('decision',{'event':'result','step':i}));q.put(('status',str(i)))
        q.put(('error','failure'));q.put(('done',None))
        self.assertLessEqual(q.qsize(),26);self.assertGreater(q.snapshot()['dropped_presentation_only'],1900)
        rows=[]
        while q.qsize():rows.append(q.get_nowait())
        self.assertEqual(rows[0][0],'connected');self.assertEqual(rows[-2:],[('error','failure'),('done',None)])
        self.assertEqual([v['step'] for k,v in rows if k=='decision'][-1],1999)
        with self.assertRaises(queue.Empty):q.get_nowait()
    def test_telemetry_is_coalesced_without_reordering_a_later_result(self):
        q=UIEventQueue()
        q.put(('decision',{'event':'perception','step':1}));q.put(('decision',{'event':'result','step':1}));q.put(('decision',{'event':'perception','step':2}))
        self.assertEqual(q.get_nowait()[1]['event'],'result');self.assertEqual(q.get_nowait()[1]['step'],2)
    def test_dashboard_geometry_uses_saved_ratios_not_content_width(self):
        self.assertEqual(dashboard_sashes(1000), (220,720))
        self.assertEqual(dashboard_sashes(1600,[.2,.7]),(320,1120))
        for value in ([float('nan'),.8],[],['x',1],None):self.assertEqual(dashboard_sashes(1000,value),(220,720))
    def test_graph_turn_counter_uses_real_step_after_skipped_presentation_events(self):
        traces=LearningTraces();traces.update({'event':'result','step':1758});traces.update({'event':'result','step':1780})
        self.assertEqual(traces.turn,1780);self.assertEqual(len(traces.samples),2)
    def test_visible_log_has_character_limit_in_addition_to_line_limit(self):
        widget=Mock();widget.index.side_effect=['40.0','100.0','15.0'];widget.xview.return_value=(0,1);widget.count.return_value=(250000,)
        append_text(widget,'x'*50000)
        self.assertLess(len(widget.insert.call_args.args[1]),12100)
        widget.delete.assert_called_with('1.0','15.0');widget.yview.assert_called_with('26.0')
    def test_real_late_run_inventory_fits_both_request_budgets_without_losing_current_text(self):
        f=json.loads((FIXTURES/'long-run-supervision-1793112198597735500.json').read_text());l=StateLedger()
        l.observe(f['ocr'],f['state'],{'location':'dungeon'},{},{'revision':0,'text':'アイテム優先'},0,1782)
        source=l.snapshot();source.update(last_observed_inventory=f['inventory'],inventory_observed_now=True)
        client=LocalLLM(Settings(goal=f['goal'],window_title='トルネコ'))
        def capture(this,image,system,prompt,schema):
            original=copy.deepcopy(prompt);b=bounded_request_prompt(prompt);t=context_retry_prompt(b)
            self.assertLessEqual(len(json.dumps(t,ensure_ascii=False,separators=(',',':'))),5000)
            for obj in (b,t):
                self.assertEqual(obj['goal'],f['goal']);self.assertEqual(obj['operator_guidance']['text'],'アイテム優先')
                self.assertEqual(obj['state_ledger']['current']['text'],source['current']['text'])
                inv=obj['state_ledger']['inventory'];names=[dict(zip(inv['columns'],r))['display_name'] for r in inv['rows']] if inv.get('columns') else [r['display_name'] for r in inv['rows']]
                self.assertEqual(names,[r['display_name'] for r in f['inventory']['rows']])
            self.assertEqual(prompt,original)
            raise RuntimeError('request captured')
        with patch.object(LocalLLM,'_request',capture):
            with self.assertRaisesRegex(RuntimeError,'request captured'):client.supervise(source,None,threading.Event())
    def test_unknown_cursor_ends_incomplete_audit_instead_of_reopening_inventory(self):
        from autoplayer.operational_control import OperationalControl
        from autoplayer.controls import REFERENCE
        from autoplayer.core import Decision
        from test_supervision import proposal
        f=json.loads((FIXTURES/'inventory-loop-restart-1793137568903748700.json').read_text())
        effects=Mock(bindings=REFERENCE['keys']);effects.keys.side_effect=lambda role:{'confirm':['num1'],'cancel':['num2'],'menu':['num0']}.get(role,[])
        actor=OperationalControl(Settings(),effects);s=f['state'];l=StateLedger()
        l.observe({},s,{'location':'dungeon'},{},{'revision':0},0,0);actor.inventory_audit.prepare({**s,'eligible':True},l.current,{},None)
        l.observe(f['inventory'],s,{'location':'dungeon'},{},{'revision':0},0,5)
        base=Decision('menu','','','',.8,'continue',[],80,600);plan=proposal();plan['skill']='inspect_inventory'
        d=actor.interface(base,f['inventory'],s,plan,l,[])
        self.assertEqual(d.keys,['num2']);self.assertIsNone(actor.inventory_audit.active);self.assertEqual(actor.inventory_audit.last['status'],'cursor_unconfirmed')
        l.observe(f['main'],s,{'location':'dungeon'},{},{'revision':0},0,6)
        d=actor.interface(base,f['main'],s,plan,l,[])
        self.assertEqual(d.keys,['num2']);self.assertGreater(actor.inventory_audit.next_step,6)
    def test_sash_layout_is_restored_on_window_resize_and_defers_during_drag(self):
        from autoplayer.ui import Application
        app=Application.__new__(Application);app.dashboard_dragging=True;app.dashboard_split=Mock();app.ui_state={'dashboard_ratios':[.2,.7]}
        app.apply_dashboard_layout();app.dashboard_split.sashpos.assert_not_called()
        app.dashboard_dragging=False;app.dashboard_split.winfo_width.return_value=1500;app.apply_dashboard_layout()
        self.assertEqual(app.dashboard_split.sashpos.call_args_list[0].args,(0,300));self.assertEqual(app.dashboard_split.sashpos.call_args_list[1].args,(1,1050))
    def test_graph_vectors_do_not_rebuild_for_each_live_frame(self):
        from autoplayer.ui import Application
        from PIL import Image
        app=Application.__new__(Application);app.overlay_enabled=SimpleNamespace(get=lambda:True);app.overlay_layers={};app.preview_bounds=(0,0,640,360);app.preview_image=Image.new('RGB',(640,360));app.shown_frame=object();app.traces=Mock(turn=240);app.live=Mock()
        with patch('PIL.ImageTk.PhotoImage') as photo,patch('autoplayer.ui.time.monotonic',side_effect=[1.,1.05,1.3]):
            app.draw_learning_graph();app.shown_frame=object();app.draw_learning_graph();app.shown_frame=object();app.draw_learning_graph()
        app.traces.paint.assert_called_once();self.assertEqual(photo.call_count,1);photo.return_value.paste.assert_called_once()
