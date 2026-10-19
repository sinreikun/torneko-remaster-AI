import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from autoplayer.core import LocalLLM,Settings,parse_decision
from autoplayer.knowledge import KnowledgeEngine
from autoplayer.ocr import ScreenOCR
from test_agent import change

def ocr(text):return {'status':'ok','frame_sha256':'frame1','lines':[{'text':text}],'confidence':None}

class KnowledgeTests(unittest.TestCase):
    def test_original_rule_not_confirmed_for_remaster(self):
        context=KnowledgeEngine().reconcile(ocr('ちょっとステキなリマスター 幸せの箱 HP 3/30'))
        rule=next(f for f in context['facts'] if f['id']=='main-goal')
        self.assertEqual(rule['applicability'],'cross_edition_hypothesis')
        self.assertEqual(rule['verification'],'term_observed_only')
        self.assertEqual(context['metrics']['hp']['values'],[3,30])
    def test_ambiguous_or_impossible_ocr_not_current_statistics(self):
        for text in ['HP 3/30 HP 9/30','HP 35/30','HP 3/0','満腹度 130%']:
            self.assertFalse(KnowledgeEngine().reconcile(ocr(text))['metrics'])
    def test_unavailable_ocr_does_not_invent_text(self):
        context=KnowledgeEngine().reconcile({'status':'unavailable','lines':[{'text':'HP 3/30'}]})
        self.assertEqual(context['metrics'],{});self.assertEqual(context['edition'],'unknown')
    def test_corrections_require_exact_current_ocr(self):
        engine=KnowledgeEngine();ctx=engine.reconcile(ocr('ゲームバランス 調整あり'))
        base={'fact_id':'remaster-balance','verdict':'contradicted','quote':'not actually read','reason':'test'}
        d=parse_decision(json.dumps(change(goal_success='HP visibly restored',goal_evidence=['ゲームバランス'],knowledge_assessments=[base])),Settings())
        state=engine.accept(d,ctx);self.assertEqual(state['knowledge_revisions'],{})
        base['quote']='調整あり';d.knowledge_assessments=[base]
        state=engine.accept(d,ctx);self.assertEqual(state['knowledge_revisions']['remaster-balance']['frame_sha256'],'frame1')
        self.assertEqual(state['autonomous_goal']['success_condition'],'HP visibly restored')
        self.assertEqual(state['autonomous_goal']['status'],'model_proposed')
    def test_llm_receives_sources_and_ocr_and_sets_goal(self):
        cfg=Settings(window_title='トルネコ');llm=LocalLLM(cfg);calls=[]
        llm.ocr.read=lambda image:ocr('HP 3/30 満腹度 5%')
        value=change(subgoal='Find safe healing',goal_success='HP visibly above 20 and no adjacent enemy')
        def request(image,system,prompt,schema):calls.append(prompt);return json.dumps(value)
        llm._request=request;llm.decide(b'frame',[],False)
        knowledge=calls[0]['source_knowledge']
        self.assertEqual(knowledge['metrics']['hp']['values'],[3,30]);self.assertTrue(knowledge['sources'])
        self.assertEqual(llm.take_state()['autonomous_goal']['objective'],'Find safe healing')
    def test_other_game_does_not_receive_torneko_rules(self):
        llm=LocalLLM(Settings(window_title='Other game'));prompts=[]
        llm._request=lambda image,system,prompt,schema:(prompts.append(prompt) or json.dumps(change()))
        llm.decide(b'frame',[],False);self.assertIsNone(prompts[0]['source_knowledge'])
    def test_actual_ocr_windows(self):
        if os.name!='nt': self.skipTest('requires Windows Media OCR')
        from PIL import Image,ImageDraw,ImageFont
        import io
        image=Image.new('RGB',(1100,220),'white');draw=ImageDraw.Draw(image)
        font=ImageFont.truetype(str(Path(os.environ['WINDIR'])/'Fonts/arial.ttf'),64)
        draw.text((30,50),'TITLE PRESS ENTER',fill='black',font=font)
        stream=io.BytesIO();image.save(stream,format='PNG');record=ScreenOCR().read(stream.getvalue())
        self.assertEqual(record['status'],'ok',record)
        text=' '.join(line['text'] for line in record['lines']).upper()
        self.assertIn('TITLE',text);self.assertIn('ENTER',text)
        self.assertTrue(record['frame_sha256']);self.assertIsNone(record['confidence'])
