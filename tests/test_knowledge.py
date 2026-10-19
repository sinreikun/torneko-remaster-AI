import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from autoplayer.core import LocalLLM,Settings,parse_decision
from autoplayer.knowledge import KnowledgeEngine
from autoplayer.ocr import ScreenOCR
from test_agent import change,OBSERVED

def ocr(text):return {'status':'ok','frame_sha256':'frame1','lines':[{'text':text}],'confidence':None}

class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        observer=patch.object(LocalLLM,'observe_scene',return_value=OBSERVED)
        observer.start();self.addCleanup(observer.stop)
    def test_original_rule_not_confirmed_for_remaster(self):
        context=KnowledgeEngine().reconcile(ocr('ちょっとステキなリマスター 幸せの箱 HP 3/30'))
        rule=next(f for f in context['facts'] if f['id']=='main-goal')
        self.assertEqual(rule['applicability'],'cross_edition_hypothesis')
        self.assertEqual(rule['verification'],'term_observed_only')
        self.assertEqual(context['metrics']['hp']['values'],[3,30])
    def test_japanese_ocr_word_spaces_match_without_rewriting_quotes(self):
        context=KnowledgeEngine().reconcile(ocr('ちょっとステキ な リマスター 幸 せ の 箱'))
        rule=next(f for f in context['facts'] if f['id']=='main-goal')
        self.assertIn('幸せの箱',rule['screen_match'])
        self.assertEqual(context['ocr']['lines'][0]['text'],'ちょっとステキ な リマスター 幸 せ の 箱')
        self.assertEqual(context['edition'],'remaster')
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
        value=change(subgoal='安全に回復する方法を探す',goal_success='HPが20を超え、隣接した敵がいない画面を確認する')
        def request(image,system,prompt,schema):calls.append(prompt);return json.dumps(value)
        llm._request=request;llm.decide(b'frame',[],False)
        knowledge=calls[0]['source_knowledge']
        self.assertEqual(knowledge['metrics']['hp']['values'],[3,30]);self.assertTrue(knowledge['sources'])
        self.assertEqual(llm.take_state()['autonomous_goal']['objective'],'安全に回復する方法を探す')
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

class Diagnostic5345KnowledgeTests(unittest.TestCase):
    def test_spaced_hp_keeps_exact_quote_and_reviews_satisfied_goal(self):
        engine=KnowledgeEngine()
        context=engine.reconcile(ocr('1 階 HP 1 5 / 1 5 満腹度 9 3 %'))
        self.assertEqual(context['metrics']['hp']['values'],[15,15])
        self.assertEqual(context['metrics']['hp']['quote'],'HP 1 5 / 1 5')
        self.assertEqual(context['metrics']['fullness']['values'],[93])
        self.assertTrue(context['goal_review']['hp_full'])
        self.assertIn('すでに満たされています',context['goal_review']['instruction'])
    def test_split_numbers_do_not_cross_lines_and_conflicts_stay_ambiguous(self):
        engine=KnowledgeEngine()
        self.assertNotIn('hp',engine.reconcile(ocr('HP 1\n5 / 15'))['metrics'])
        self.assertNotIn('hp',engine.reconcile(ocr('HP 1 3 / 1 5 HP 15/15'))['metrics'])
    def test_future_hp_is_not_saved_as_current_progress_or_claim(self):
        engine=KnowledgeEngine();context=engine.reconcile(ocr('HP 1 3 / 1 5'))
        decision=parse_decision(json.dumps(change(progress=['HPが13/15から15/15に回復'],memory_updates=[{'key':'HP','value':'15/15','confidence':.9},{'key':'route','value':'通路が見える','confidence':.7}])),Settings())
        state=engine.accept(decision,context)
        self.assertEqual(decision.progress,[])
        self.assertEqual([item['key'] for item in decision.memory_updates],['route'])
        self.assertEqual(len(state['unconfirmed_model_reports']),2)
        self.assertEqual(state['ocr']['lines'][0]['text'],'HP 1 3 / 1 5')
