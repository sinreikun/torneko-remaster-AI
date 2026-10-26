"""UI presentation and activity/strategy boundaries, not game emulation."""
import json,unittest
from unittest.mock import Mock
from autoplayer.telemetry import RecognitionState,paint_grid,grid_layout,grid_cell
from autoplayer.guidance import OperatorGuidance
from autoplayer.core import LocalLLM,Settings

class TelemetryTests(unittest.TestCase):
    def test_resized_grid_fits_and_clicks_keep_cell_coordinates(self):
        for width,height in ((180,110),(340,210),(710,440),(1000,220)):
            layout=grid_layout(width,height);x,y,unit=layout
            self.assertGreater(unit,0);self.assertGreaterEqual(x,0);self.assertGreaterEqual(y,0)
            self.assertLessEqual(x+54*unit,width);self.assertLessEqual(y+32*unit,height)
            for col,row in ((0,0),(27,16),(53,31)):
                self.assertEqual(grid_cell(x+(col+.5)*unit,y+(row+.5)*unit,layout),(col,row))
            self.assertIsNone(grid_cell(x-1,y,layout))
            self.assertIsNone(grid_cell(x+54*unit,y,layout))

    def test_status_and_runtime_are_separate_logical_wrappable_lines(self):
        lines=RecognitionState().summary().splitlines()
        self.assertIn('階 ',lines[0]);self.assertIn('所持金 ',lines[0])
        self.assertTrue(lines[1].startswith('次のLLM：'));self.assertIn('地図：',lines[1])

    def test_fixed_grid_accumulates_known_and_preserves_icon_priority(self):
        s=RecognitionState();m={'status':'observed','grid_size':[54,32],'player_cell':[2,3],'accumulated_cells':[[1,3],[2,3]],'known_cells':[[0,0],[1,0]],'items':[[1,0]],'stairs':[[0,1]],'enemies':[[-1,0]]}
        s.update({'event':'perception','policy_state':{'minimap':m,'metrics':{'hp':{'values':[9,15]}}},'anticipation':{'remaining':3}})
        self.assertEqual(s.grid(),{(1,3):'enemy',(2,3):'player',(3,3):'item',(2,4):'stairs'})
        self.assertIn('9/15',s.summary());self.assertEqual(s.remaining,3)
        c=Mock();paint_grid(c,{});self.assertEqual(c.create_rectangle.call_count,54*32)
    def test_result_uses_latest_values_and_clears_stale_object_boxes(self):
        s=RecognitionState();s.update({'event':'perception','world_observation':{'objects':[{'id':'object_1'}]},'policy_state':{'minimap':{},'metrics':{'hp':{'values':[10,15]}}}})
        s.update({'event':'result','policy_after':{'minimap':{'status':'unavailable'},'metrics':{}},'forecast_evaluation':{'status':'mismatch','remaining':4}})
        self.assertEqual(s.metrics,{});self.assertEqual(s.objects,[]);self.assertEqual(s.remaining,0);self.assertIn('不明',s.summary())
    def test_overlapping_inference_lamp_and_stop(self):
        s=RecognitionState()
        for request in ('a','b'):s.update({'event':'llm_activity','request_id':request,'active':True})
        s.update({'event':'llm_activity','request_id':'a','active':False});self.assertTrue(s.thinking)
        s.update({'event':'llm_activity','request_id':'b','active':False});self.assertFalse(s.thinking)
        s.update({'event':'llm_activity','request_id':'a','active':True});s.update({'event':'session_end'});self.assertFalse(s.thinking)
    def test_request_lamp_is_reset_on_exception(self):
        llm=LocalLLM(Settings());rows=[];llm.activity=rows.append;llm._request_impl=Mock(side_effect=TimeoutError())
        with self.assertRaises(TimeoutError):llm._request(None,'',{})
        self.assertEqual([r['active'] for r in rows],[True,False]);self.assertEqual(rows[0]['request_id'],rows[1]['request_id'])
    def test_free_text_guidance_is_versioned_bounded_and_clearable(self):
        g=OperatorGuidance();g.set('慎重に、アイテムを優先して');a=g.snapshot();g.set('階段を探して')
        self.assertGreater(g.snapshot()['revision'],a['revision']);self.assertEqual(a['text'],'慎重に、アイテムを優先して')
        with self.assertRaises(ValueError):g.set('x'*2001)
        g.set('');self.assertEqual(g.snapshot()['text'],'')

    def test_strategy_text_survives_map_prose_filter_and_context_compaction(self):
        from unittest.mock import MagicMock,patch
        client=LocalLLM(Settings());client.context_compaction=True
        response=MagicMock();response.__enter__.return_value=response
        response.read.return_value=json.dumps({'message':{'content':'{}'}}).encode()
        opener=Mock();opener.open.return_value=response
        text='慎重に。地図のアイテムを優先して階段へ向かう。'
        with patch('autoplayer.core.urllib.request.build_opener',return_value=opener):
            client._request(None,'日本語',{'operator_guidance':{'revision':3,'text':text},'goal':'探索'})
        sent=json.loads(opener.open.call_args.args[0].data)
        prompt=json.loads(sent['messages'][1]['content'])
        self.assertEqual(prompt['operator_guidance']['text'],text)

class OverlayTests(unittest.TestCase):
    def test_target_visibility_checks_pid_foreground_and_client_origin(self):
        import ctypes as C
        from ctypes import wintypes as W
        from autoplayer.overlay import OverlayAPI
        api=OverlayAPI.__new__(OverlayAPI);api.C=C;api.W=W;api.u=Mock()
        api.u.IsWindow.return_value=True;api.u.IsIconic.return_value=False
        api.u.GetForegroundWindow.return_value=123;api.u.IsChild.return_value=False
        def pid(hwnd,p):C.cast(p,C.POINTER(W.DWORD)).contents.value=7
        def rect(hwnd,p):r=C.cast(p,C.POINTER(W.RECT)).contents;r.right=1280;r.bottom=720;return True
        def origin(hwnd,p):r=C.cast(p,C.POINTER(W.POINT)).contents;r.x=-1280;r.y=20;return True
        api.u.GetWindowThreadProcessId.side_effect=pid;api.u.GetClientRect.side_effect=rect;api.u.ClientToScreen.side_effect=origin
        self.assertEqual(api.target_rect(123,7),(-1280,20,1280,720))
        self.assertIsNone(api.target_rect(123,8))
        api.u.GetForegroundWindow.return_value=999;self.assertIsNone(api.target_rect(123,7))
        api.u.GetForegroundWindow.return_value=123;api.u.IsIconic.return_value=True;self.assertIsNone(api.target_rect(123,7))
    def test_layer_switching_preserves_independent_options(self):
        from autoplayer.overlay import GameOverlay
        o=GameOverlay.__new__(GameOverlay);o.layers={'map':True,'status':True,'objects':True,'forecast':True};o.last_revision=99
        o.set_layers({'map':False,'objects':False});self.assertFalse(o.layers['map']);self.assertTrue(o.layers['status']);self.assertTrue(o.layers['forecast']);self.assertEqual(o.last_revision,-1)

class GuidanceIntegrationTests(unittest.TestCase):
    def test_changed_note_during_analysis_discards_pending_input(self):
        import tempfile
        from pathlib import Path
        from autoplayer.core import Controller
        from test_agent import Backend,Script,change
        g=OperatorGuidance('慎重に')
        class ChangingScript(Script):
            def decide(self,*args):
                d=super().decide(*args)
                if len(self.calls)==1:g.set('アイテムを優先')
                return d
        script=ChangingScript([change(action={'keys':['d'],'hold_ms':80,'wait_ms':0})]*2);script.operator_guidance=g
        b=Backend()
        with tempfile.TemporaryDirectory() as tmp:
            agent=Controller(Settings(max_steps=2),b,script,lambda _:None,Path(tmp)/'run.jsonl');agent.run()
            self.assertEqual(len(b.actions),1)
            rows=[json.loads(x) for x in agent.journal.read_text().splitlines()]
            self.assertTrue(any(r.get('reason')=='operator_guidance_changed' for r in rows))
    def test_guided_model_action_is_not_overwritten_but_exhaustion_still_applies(self):
        import tempfile
        from pathlib import Path
        from autoplayer.core import parse_decision
        from autoplayer.memory import MemoryStore
        from autoplayer.policy import ExperiencePolicy
        from test_agent import change
        from test_policy import state
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Settings();m=MemoryStore(Path(tmp)/'m.sqlite',cfg)
            try:
                p=ExperiencePolicy(m,cfg);s=state();d=parse_decision(json.dumps(change(scene='gameplay',action={'keys':['d'],'hold_ms':80,'wait_ms':0})),cfg)
                action,choice=p.choose(d,s,stable=True,respect_proposal=True)
                self.assertEqual(action.keys,['d']);self.assertEqual(choice['source'],'operator_guided_model')
                node=p.routes.node(s);p.routes.no_effect[node,'d']=2
                _,choice=p.choose(d,s,stable=True,respect_proposal=True)
                self.assertNotEqual(choice['source'],'operator_guided_model')
            finally:m.close()

class EveryRequestGuidanceTests(unittest.TestCase):
    def test_every_role_gets_current_note_even_if_prompt_contains_stale_note(self):
        from unittest.mock import MagicMock,patch
        for provider in ('ollama','openai'):
            client=LocalLLM(Settings(provider=provider));g=OperatorGuidance('慎重に');client.operator_guidance=g
            response=MagicMock();response.__enter__.return_value=response
            response.read.side_effect=lambda *_:json.dumps({'message':{'content':'{}'},'choices':[{'message':{'content':'{}'}}]}).encode()
            opener=Mock();opener.open.return_value=response
            with patch('autoplayer.core.urllib.request.build_opener',return_value=opener):
                for index,task in enumerate(('scene_observation','decision','review','decision_protocol_repair','verification')):
                    note=f'地図のアイテムを優先 {index}';g.set(note)
                    client._request(None,'日本語',{'task':task,'operator_guidance':{'text':'old'}})
                    body=json.loads(opener.open.call_args.args[0].data)
                    self.assertIn(note,body['messages'][0]['content'])
                    payload=body['messages'][1]['content'];payload=payload if isinstance(payload,str) else payload[0]['text']
                    self.assertEqual(json.loads(payload)['operator_guidance']['text'],note)
                g.set('');client._request(None,'日本語',{'operator_guidance':{'text':'old'}})
                body=json.loads(opener.open.call_args.args[0].data)
                self.assertNotIn('old',body['messages'][0]['content'])
                self.assertFalse(client.request_metrics[-1]['operator_guidance_applied'])
    def test_identical_note_does_not_invalidate_a_plan_repeatedly(self):
        g=OperatorGuidance();g.set('慎重に');first=g.snapshot()['revision']
        self.assertEqual(g.set('慎重に'),first);self.assertEqual(g.set('  慎重に  '),first)
