"""Transport/control contracts for observed empty responses; no game simulation."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from autoplayer.core import Settings,LocalLLM,Controller
from test_agent import Script,Backend,change

class ResponseRecoveryTests(unittest.TestCase):
    def client(self,replies):
        opened=[]
        def open_request(request,timeout):
            opened.append(json.loads(request.data))
            return io.BytesIO(json.dumps(replies.pop(0)).encode())
        return LocalLLM(Settings()),SimpleNamespace(open=open_request),opened
    def test_empty_schema_response_retries_once_and_retains_generic_json(self):
        client,opener,requests=self.client([{'model':'','message':{'content':''}},
            {'message':{'content':'{"ok": true}'}},{'message':{'content':'{"ok": true}'}}])
        schema={'type':'object','required':['ok']}
        with patch('urllib.request.build_opener',return_value=opener):
            self.assertTrue(json.loads(client._request(b'actual-image-bytes','日本語',{'task':'scene_observation'},schema))['ok'])
            client._request(None,'日本語',{'task':'self_improvement_review'},schema)
        self.assertEqual([r['format'] for r in requests],[schema,'json','json'])
        self.assertEqual(requests[0]['messages'][1]['images'],requests[1]['messages'][1]['images'])
        self.assertEqual(json.loads(requests[1]['messages'][1]['content'])['required_output_schema'],schema)
        self.assertEqual(client.request_metrics[0]['recovery'],'retry_generic_json')
        self.assertEqual(client.request_metrics[1]['response_format'],'json')
    def test_no_infinite_retry_for_invalid_generic_response(self):
        client,opener,requests=self.client([{'message':{'content':''}},{'message':{'content':'not json'}}])
        with patch('urllib.request.build_opener',return_value=opener),self.assertRaises(ValueError):
            client._request(None,'日本語',{}, {'type':'object'})
        self.assertEqual(len(requests),2)
    def test_valid_json_still_requires_existing_action_validation(self):
        from autoplayer.core import parse_decision
        invalid=change(action={'keys':['enter'],'hold_ms':80,'wait_ms':0})
        client,opener,requests=self.client([{'message':{'content':''}},{'message':{'content':json.dumps(invalid)}}])
        with patch('urllib.request.build_opener',return_value=opener):
            raw=client._request(None,'日本語',{}, {'type':'object'})
        with self.assertRaises(ValueError):parse_decision(raw,Settings())
    def test_unavailable_observer_has_a_bounded_no_input_recovery(self):
        backend=Backend();script=Script([change(action={'keys':[],'hold_ms':1,'wait_ms':0})]*3)
        script.take_state=lambda:{'scene_observation':{'status':'unavailable','reason':'empty response'}}
        with tempfile.TemporaryDirectory() as tmp:
            controller=Controller(Settings(max_steps=100,recovery_attempts=0),backend,script,lambda row:None,Path(tmp)/'run.jsonl')
            with self.assertRaisesRegex(RuntimeError,'LLM'):controller.run()
            rows=[json.loads(line) for line in controller.journal.read_text().splitlines()]
        self.assertEqual(backend.actions,[]);self.assertEqual(len(script.calls),3)
        self.assertEqual(rows[-1]['status'],'llm_unavailable');self.assertEqual(backend.releases,1)
    def test_valid_unknown_scene_is_not_a_service_failure(self):
        backend=Backend();script=Script([change(scene='unknown',action={'keys':[],'hold_ms':1,'wait_ms':0})]*4)
        script.take_state=lambda:{'scene_observation':{'status':'model_observation_unverified','scene_type':'unknown'}}
        with tempfile.TemporaryDirectory() as tmp:
            Controller(Settings(max_steps=4,recovery_attempts=0),backend,script,lambda row:None,Path(tmp)/'run.jsonl').run()
        self.assertEqual(len(script.calls),4);self.assertEqual(backend.actions,[])

class PixelGeometryTests(unittest.TestCase):
    def test_only_small_gaps_are_joined_and_markers_keep_strict_components(self):
        from autoplayer.minimap import components
        points={(0,0),(2,0),(8,0)}
        self.assertEqual(len(components(points)),3)
        self.assertEqual(sorted(len(c) for c in components(points,gap=2)),[1,2])
    def test_unsupported_edge_pixels_do_not_change_shape(self):
        from autoplayer.minimap import quantized_shape
        patch={(0,0),(0,1),(1,0),(1,1)}
        self.assertEqual(quantized_shape(patch,0,0,6),[(0,0)])
        self.assertEqual(quantized_shape(patch|{(6,0)},0,0,6),[(0,0)])

class DecisionRepairTests(unittest.TestCase):
    def test_invalid_status_gets_one_explicit_correction_not_silent_conversion(self):
        from test_agent import OBSERVED
        client=LocalLLM(Settings());client.ocr.read=Mock(return_value={'status':'unavailable','lines':[]})
        client.observe_scene=Mock(return_value=OBSERVED)
        client._request=Mock(side_effect=[json.dumps(change(status='unknown')),json.dumps(change())])
        result=client.decide(b'frame',[],False)
        self.assertEqual(result.status,'continue');self.assertEqual(client._request.call_count,2)
        repair=client._request.call_args_list[1].args[2]
        self.assertEqual(repair['protocol_error'],'invalid status')
        self.assertEqual(repair['task'],'decision_protocol_repair')
    def test_invalid_correction_is_not_executed_or_retried_forever(self):
        from test_agent import OBSERVED
        client=LocalLLM(Settings());client.ocr.read=Mock(return_value={'status':'unavailable','lines':[]})
        client.observe_scene=Mock(return_value=OBSERVED)
        client._request=Mock(return_value=json.dumps(change(status='model_proposed')))
        with self.assertRaisesRegex(ValueError,'invalid status'):client.decide(b'frame',[],False)
        self.assertEqual(client._request.call_count,2)

class HudLabelTests(unittest.TestCase):
    def test_observed_hp_label_noise_does_not_change_numeric_evidence(self):
        from autoplayer.knowledge import KnowledgeEngine
        for raw in ('HPi 1 5 / 1 5','HPi 、 1 5 / 1 5','HPi ・ 、 1 5 / 1 5','HPt*1 5 / 1 5'):
            with self.subTest(raw=raw):
                result=KnowledgeEngine().reconcile({'status':'ok','lines':[{'text':raw}]})['metrics']['hp']
                self.assertEqual(result['values'],[15,15]);self.assertEqual(result['quote'],raw)
        for raw in ('HP -1 5 / 1 5','HP 15 / ー ぎ 鉙','HPi 16 / 15','HPi 1\n5 / 15','HPitem15/15'):
            self.assertNotIn('hp',KnowledgeEngine().reconcile({'status':'ok','lines':[{'text':raw}]})['metrics'])
    def test_blue_box_approach_and_impact_claims_are_not_adopted(self):
        from autoplayer.core import parse_decision,response_quality_issue
        for text in ('青い箱に近づく','青い箱が障害物なので動かす','青い物体の上の黄色い円形の影響を評価する'):
            decision=parse_decision(json.dumps(change(subgoal=text)),Settings())
            self.assertTrue(response_quality_issue(decision,minimap_reference=True))
