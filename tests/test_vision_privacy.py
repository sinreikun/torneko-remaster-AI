"""Model-boundary payload and pixel exclusion contracts; no game simulation."""
import io,json,unittest
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
from autoplayer.vision_privacy import OverlayExclusion,model_data,ocr_without_regions
from autoplayer.core import LocalLLM,Settings

class ExclusionTests(unittest.TestCase):
    def png(self):
        out=io.BytesIO();Image.new('RGB',(100,100),(10,20,30)).save(out,format='PNG');return out.getvalue()
    def evidence(self):return {'status':'observed','bbox':[50,50,60,60],'cell_size':1,'source_size':[100,100],'analysis_size':[100,100]}
    def test_only_excluded_pixels_change_and_source_is_unchanged(self):
        original=self.png();out,meta=OverlayExclusion().prepare(original,self.evidence(),required=True)
        image=Image.open(io.BytesIO(out));self.assertEqual(image.getpixel((55,55)),(96,96,96))
        self.assertEqual(image.getpixel((0,0)),(10,20,30));self.assertEqual(original,self.png())
        self.assertTrue(meta['hidden_regions'])
    def test_known_region_is_used_during_detection_loss_but_not_after_size_change(self):
        exclusion=OverlayExclusion();exclusion.prepare(self.png(),self.evidence(),True)
        with patch('autoplayer.minimap.read_minimap',return_value={'status':'unavailable'}):
            _,meta=exclusion.prepare(self.png(),required=True);self.assertTrue(meta['hidden_regions'])
            out=io.BytesIO();Image.new('RGB',(120,100)).save(out,format='PNG')
            with self.assertRaises(ValueError):exclusion.prepare(out.getvalue(),required=True)
    def test_map_mentions_are_removed_from_all_prose_and_structured_context(self):
        raw={'knowledge':'HPを確認。ミニマップの黄色は自位置。通路を探索。',
            'history':[{'subgoal':'青い箱に近づく','reason':'青い物体の影響を確認'}],
            'minimap_observation':{'player_cell':[1,2]},'controls':{'num7':'マップ表示','w':'上移動'},
            'goal':{'objective':'地図の黄色マーカーへ向かう'},'allowed_keys':['w','num7']}
        safe=model_data(raw);text=json.dumps(safe,ensure_ascii=False)
        for word in ('ミニマップ','青い箱','青い物体','num7','minimap_observation','player_cell','地図'):
            self.assertNotIn(word,text)
        self.assertIn('HPを確認',text);self.assertIn('通路を探索',text)
        self.assertIn('num7',raw['controls'])
    def test_ocr_inside_hidden_region_does_not_reenter_text_planner(self):
        native={'lines':[{'text':'HP 15/15','words':[{'box':[1,1,30,10]}]},
                         {'text':'UI glyphs','words':[{'box':[55,55,4,4]}]}]}
        safe=ocr_without_regions(native,[[50,50,65,65]])
        self.assertEqual([l['text'] for l in safe['lines']],['HP 15/15'])
        self.assertEqual(len(native['lines']),2)
    def test_actual_http_boundary_sanitizes_prompts_and_sends_masked_image(self):
        llm=LocalLLM(Settings());llm.set_context({'minimap_observation':self.evidence(),'_floor_observed':True})
        requests=[]
        def opened(request,timeout):
            requests.append(json.loads(request.data));return io.BytesIO(b'{"message":{"content":"{\\"ok\\":true}"}}')
        with patch('urllib.request.build_opener',return_value=SimpleNamespace(open=opened)):
            llm._request(self.png(),'日本語で応答。地図へ向かう。',{'task':'scene_observation','minimap_pixels':{'cell':[1,2]},'history':['青い箱へ向かう']})
        serialized=json.dumps(requests[0],ensure_ascii=False)
        for text in ('minimap_pixels','青い箱','地図へ向かう'):self.assertNotIn(text,serialized)
        import base64
        sent=Image.open(io.BytesIO(base64.b64decode(requests[0]['messages'][1]['images'][0])))
        self.assertEqual(sent.getpixel((55,55)),(96,96,96))
        self.assertTrue(llm.request_metrics[0]['input_exclusion']['hidden_regions'])

    def test_region_unknown_on_known_floor_does_not_send_original_image(self):
        llm=LocalLLM(Settings());llm.set_context({'_floor_observed':True})
        with patch('autoplayer.minimap.read_minimap',return_value={'status':'unavailable'}),patch('urllib.request.build_opener') as opener:
            with self.assertRaises(ValueError):llm._request(self.png(),'日本語',{'task':'scene_observation'})
        opener.assert_not_called()
    def test_cached_exclusion_is_not_applied_to_a_map_free_scene(self):
        exclusion=OverlayExclusion();original=self.png();exclusion.prepare(original,self.evidence(),True)
        with patch('autoplayer.minimap.read_minimap',return_value={'status':'unavailable'}):
            image,meta=exclusion.prepare(original,required=False)
        self.assertEqual(image,original);self.assertEqual(meta['hidden_regions'],[])
