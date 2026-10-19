"""Regression: identifying the next key must never count as finishing a game."""
import json
import unittest
from autoplayer.core import Settings,LocalLLM,parse_decision,VERIFY_SCHEMA
from test_agent import change

class FakeVerifier(LocalLLM):
    def __init__(self,facts,answer=None,goal='Reach VICTORY'):
        super().__init__(Settings(goal=goal));self.facts=facts;self.answer=answer;self.calls=[]
    def _request(self,png,system,prompt,schema=None):
        self.calls.append((png,system,prompt,schema))
        return json.dumps(self.facts if len(self.calls)==1 else self.answer)

class CompletionTests(unittest.TestCase):
    def test_pending_key_cannot_be_complete(self):
        value=change(status='complete',action={'keys':['enter'],'hold_ms':80,'wait_ms':800})
        with self.assertRaisesRegex(ValueError,'use status continue'): parse_decision(json.dumps(value),Settings())
    def test_title_with_goal_footer_is_not_victory(self):
        facts={'scene_type':'title','visible_text':['TITLE','Press ENTER to continue','Victory is the only goal.'],'description':'Blue title screen with an instruction.'}
        verifier=FakeVerifier(facts)
        self.assertFalse(verifier.verify_completion(b'frame')['achieved'])
        self.assertEqual(len(verifier.calls),1)
        self.assertNotIn(verifier.cfg.goal,json.dumps(verifier.calls[0][2]))
    def test_goal_agnostic_observation_then_grounded_completion(self):
        facts={'scene_type':'victory','visible_text':['VICTORY','GOAL ACHIEVED'],'description':'Green victory screen.'}
        answer={'achieved':True,'confidence':.99,'evidence':['VICTORY','GOAL ACHIEVED'],'reason':'The goal is visible.','remaining_actions':[]}
        verifier=FakeVerifier(facts,answer)
        self.assertTrue(verifier.verify_completion(b'frame')['achieved'])
        self.assertIsNone(verifier.calls[1][0])
        self.assertEqual(verifier.calls[1][2]['observations'],facts)
        self.assertEqual(verifier.calls[1][3]['properties']['evidence']['items']['enum'],['VICTORY','GOAL ACHIEVED','Green victory screen.'])
        self.assertNotIn('enum',VERIFY_SCHEMA['properties']['evidence']['items'])
    def test_native_ocr_is_independent_of_goal_and_keeps_heading_sizes(self):
        facts={'scene_type':'victory','visible_text':['VICTORY'],'description':'Green victory screen.'}
        answer={'achieved':True,'confidence':.99,'evidence':['GOAL ACHIEVED'],'reason':'Exact current words.','remaining_actions':[]}
        verifier=FakeVerifier(facts,answer)
        verifier.ocr.read=lambda image:{'status':'ok','language':'en-US','lines':[{'text':'GOAL ACHIEVED','words':[{'box':[10,20,200,60]}]}]}
        self.assertTrue(verifier.verify_completion(b'frame')['achieved'])
        self.assertEqual(verifier.calls[0][2]['independent_ocr']['lines'][0]['max_word_height'],60)
        self.assertNotIn('goal',verifier.calls[0][2])
    def test_ocr_victory_heading_reconciles_a_wrong_gameplay_category(self):
        facts={'scene_type':'gameplay','visible_text':['VICTORY','GOAL ACHIEVED'],'description':'Green victory screen.'}
        answer={'achieved':True,'confidence':.99,'evidence':['VICTORY','GOAL ACHIEVED'],'reason':'Entire goal visibly done.','remaining_actions':[]}
        verifier=FakeVerifier(facts,answer)
        verifier.ocr.read=lambda image:{'status':'ok','language':'en-US','lines':[
            {'text':'VICTORY','words':[{'box':[10,20,200,60]}]},
            {'text':'GOAL ACHIEVED','words':[{'box':[10,100,200,38]}]}]}
        value=verifier.verify_completion(b'frame')
        self.assertTrue(value['achieved'])
        self.assertEqual(value['visual_facts']['classification_conflict']['model_category'],'gameplay')
    def test_ocr_title_heading_overrules_victory_footer_and_bad_category(self):
        facts={'scene_type':'victory','visible_text':['TITLE','Victory is the only goal.'],'description':'A blue title screen.'}
        verifier=FakeVerifier(facts)
        verifier.ocr.read=lambda image:{'status':'ok','language':'en-US','lines':[
            {'text':'TITLE','words':[{'box':[10,20,200,60]}]},
            {'text':'Victory is the only goal.','words':[{'box':[10,100,200,24]}]}]}
        self.assertFalse(verifier.verify_completion(b'frame')['achieved'])
        self.assertEqual(len(verifier.calls),1)
    def test_background_measurement_is_independent_of_model_or_goal(self):
        from PIL import Image
        from autoplayer.observation import image_color_facts
        import io
        for rgb,color in [((21,90,50),'green'),((23,32,51),'blue')]:
            image=Image.new('RGB',(100,100),rgb);buffer=io.BytesIO();image.save(buffer,format='PNG')
            value=image_color_facts(buffer.getvalue())
            self.assertEqual(value['dominant_color'],color)
            self.assertEqual(value['dominant_rgb'],list(rgb))
    def test_placeholder_evidence_cannot_finish(self):
        facts={'scene_type':'victory','visible_text':['VICTORY'],'description':'Green end screen.'}
        answer={'achieved':True,'confidence':1.,'evidence':['visible evidence'],'reason':'Some next step is correct.','remaining_actions':[]}
        self.assertFalse(FakeVerifier(facts,answer).verify_completion(b'frame')['achieved'])
    def test_remaining_actions_prevent_completion(self):
        facts={'scene_type':'dialog','visible_text':['Continue'],'description':'A conversation still in progress.'}
        answer={'achieved':True,'confidence':1.,'evidence':['Continue'],'reason':'Need to proceed.','remaining_actions':['Finish the goal']}
        self.assertFalse(FakeVerifier(facts,answer).verify_completion(b'frame')['achieved'])
    def test_non_victory_goal_can_be_achieved_at_title(self):
        facts={'scene_type':'title','visible_text':['TITLE'],'description':'Title screen.'}
        answer={'achieved':True,'confidence':1.,'evidence':['TITLE'],'reason':'Requested title screen visible.','remaining_actions':[]}
        self.assertTrue(FakeVerifier(facts,answer,goal='Reach the TITLE screen').verify_completion(b'frame')['achieved'])
    def test_unknown_scene_never_proves_completion(self):
        verifier=FakeVerifier({'scene_type':'unknown','visible_text':[],'description':'Unclear screen.'})
        self.assertFalse(verifier.verify_completion(b'frame')['achieved'])

if __name__=='__main__':unittest.main()
