"""Current OCR premise and isolated-map palette contracts; no game simulation."""
import hashlib
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from PIL import Image,ImageDraw
from autoplayer.core import Settings,LocalLLM,parse_decision
from autoplayer.knowledge import KnowledgeEngine
from autoplayer.minimap import read_minimap
from test_agent import change

class CurrentEvidenceTests(unittest.TestCase):
    def test_hp_conflict_invalidates_reason_without_repairing_ocr_or_unknown_values(self):
        d=parse_decision(json.dumps(change(reason='HPが7/15で不足しているため、HPを回復する必要がある。')),Settings())
        context={'metrics':{'hp':{'values':[42,43]}}}
        self.assertIn('不一致',KnowledgeEngine.decision_grounding_issue(d,context))
        self.assertEqual(context['metrics']['hp']['values'],[42,43])
        self.assertEqual(KnowledgeEngine.decision_grounding_issue(d,{'metrics':{}}),'')
        d.reason='HPは42/43。進路を探索する。'
        self.assertEqual(KnowledgeEngine.decision_grounding_issue(d,context),'')

    def test_planner_does_not_accept_stale_hp_action_or_goal(self):
        png=b'current-frame';native={'status':'ok','frame_sha256':hashlib.sha256(png).hexdigest(),
            'lines':[{'text':s} for s in ['5階','Lv6','HP 42 / 43','満腹度46%','270G']]}
        client=LocalLLM(Settings());client.ocr=SimpleNamespace(read=Mock(return_value=native))
        client.observe_scene=Mock(return_value={'scene_type':'gameplay','visible_text':['HP 42 / 43'],
            'description':'木製の床の上にキャラクターが立っている。','player_position':'中央','open_routes':[],
            'obstacles':[],'hazards':[],'confidence':.8})
        client._request=Mock(return_value=json.dumps(change(scene='gameplay',observation='木製の床の上にキャラクターが立っている。',
            reason='HPが7/15で不足しているため、HPを回復する必要がある。',subgoal='HPを回復する',goal_success='HPが回復した表示を確認する')))
        decision=client.decide(png,[],False)
        self.assertEqual(decision.keys,[]);self.assertIn('不一致',client.take_state()['model_response_issue'])
        self.assertIsNone(client.knowledge_engine.goal)

    def test_green_palette_only_applies_to_explicit_isolated_dark_capture(self):
        def pixels(background):
            image=Image.new('RGB',(160,120),background);draw=ImageDraw.Draw(image)
            draw.rectangle((40,40,84,70),fill=(0,105,52));draw.rectangle((60,52,63,55),fill=(200,200,0))
            output=io.BytesIO();image.save(output,format='PNG');return output.getvalue()
        dark=pixels((0,0,0))
        self.assertEqual(read_minimap(dark,isolated=True)['status'],'observed')
        self.assertEqual(read_minimap(dark)['status'],'unavailable')
        self.assertEqual(read_minimap(pixels((100,80,60)),isolated=True)['status'],'unavailable')
