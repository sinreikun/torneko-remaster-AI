"""Regressions for literal OCR samples, not gameplay tests."""
import io,unittest,tempfile
from pathlib import Path
from PIL import Image
from autoplayer.interfaces import has_text_content
from autoplayer.fast_learning import FastLearningGate
from autoplayer.interface_responses import layout_group
from autoplayer.policy import ExperiencePolicy
from autoplayer.memory import MemoryStore
from autoplayer.core import Settings


def native(text):return {'status':'ok','lines':[{'text':text,'confidence':.99,'words':[{'box':[10,10,20,20]}]}]}
class TextContentTests(unittest.TestCase):
    def test_symbol_or_number_alone_does_not_supply_text_context(self):
        for text in ('`','8','123','15/15','▼','。！',''):
            self.assertFalse(has_text_content(native(text)),text)
        for text in ('話す','はい','HP 15/15','1階','Lv 3','Continue'):
            self.assertTrue(has_text_content(native(text)),text)
    def test_isolated_ocr_returns_to_visual_reasoning(self):
        gate=FastLearningGate(128);state={'visual':[0]*192};gate.evaluate(0,native(''),state,{})
        for turn,text in enumerate(('`','8'),1):
            self.assertEqual(gate.evaluate(turn,native(text),state,{})['reason'],'visual_evidence_gap')
        self.assertFalse(gate.evaluate(3,native('話す'),state,{})['visual_fallback'])
    def test_single_glyphs_are_not_transfer_layouts(self):
        for text in ('`','8'):self.assertIsNone(layout_group(native(text),[100,100],{}))
        self.assertIsNotNone(layout_group(native('話す'),[100,100],{}))
    def test_observation_keeps_raw_ocr_but_does_not_enable_q_learning_from_number(self):
        output=io.BytesIO();Image.new('RGB',(64,64),(50,100,150)).save(output,format='PNG')
        with tempfile.TemporaryDirectory() as t:
            cfg=Settings();memory=MemoryStore(Path(t)/'memory.sqlite',cfg)
            try:
                policy=ExperiencePolicy(memory,cfg);ocr=native('8');state=policy.observe(output.getvalue(),ocr)
                self.assertFalse(state['observable']);self.assertFalse(state['text_reliable']);self.assertEqual(ocr['lines'][0]['text'],'8')
                other=io.BytesIO();Image.new('RGB',(64,64),(220,220,220)).save(other,format='PNG')
                self.assertNotEqual(state['id'],policy.observe(other.getvalue(),ocr)['id'])
            finally:memory.close()
