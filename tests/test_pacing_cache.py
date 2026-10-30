"""Timing and exact-byte OCR reuse contracts; no game simulation."""
import copy
import json
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock
from autoplayer.core import Settings,parse_decision
from autoplayer.ocr import ScreenOCR
from autoplayer.pacing import AdaptivePacing
from test_agent import change
from test_policy import state,receipt_row


class PacingTests(TestCase):
    def setUp(self):
        self.pace=AdaptivePacing(5000)
        self.decision=parse_decision(json.dumps(change(scene='gameplay',action={'keys':['w'],'hold_ms':80,'wait_ms':600})),Settings())
        self.s=state();self.choice={'source':'learned_policy'}

    def sample(self,kind='normal',activity=100,damage=False):
        trial=self.pace.apply(self.decision,self.s,self.choice,True)
        after=copy.deepcopy(self.s)
        if damage:after['metrics']['hp']['values']=[14,15]
        row=receipt_row(policy_state=self.s,policy_after=after,policy_learning={'status':'trained','net_signals':{'position':1}})
        return self.pace.record(trial,row,SimpleNamespace(kind=kind,last_activity_ms=activity))

    def test_decreases_only_after_six_stable_samples_and_keeps_margin_and_floor(self):
        for _ in range(5):self.sample()
        self.assertEqual(self.pace.wait,600)
        self.sample();self.assertEqual(self.pace.wait,550)
        for _ in range(100):self.sample()
        self.assertEqual(self.pace.wait,400)
        self.sample(activity=900)
        self.assertGreaterEqual(self.pace.wait,1150)

    def test_damage_timeout_missing_timing_and_no_change_restore_margin(self):
        for args in ({'damage':True},{'kind':'timeout'},{'activity':None},{'kind':'no_change'}):
            result=self.sample(**args)
            self.assertEqual(result['status'],'margin_restored')
            self.assertGreaterEqual(self.pace.wait,1200)
            self.assertLessEqual(self.pace.wait,2000)
            self.assertFalse(self.pace.samples)

    def test_hazards_unknown_state_nonmovement_and_unstable_do_not_shorten_waits(self):
        for kind in ('enemy','unknown','nonmovement','unstable','model'):
            s=copy.deepcopy(self.s);decision=copy.deepcopy(self.decision);choice=dict(self.choice)
            if kind=='enemy':s['minimap']['enemies']=[(1,0)]
            if kind=='unknown':s['eligible']=False
            if kind=='nonmovement':decision.keys=['num1']
            if kind=='model':choice['source']='model_nonmovement'
            self.assertIsNone(self.pace.apply(decision,s,choice,kind!='unstable'))
            self.assertEqual(decision.wait_ms,600)

    def test_context_changes_reset_and_user_cap_is_preserved(self):
        self.sample(kind='timeout');self.assertEqual(self.pace.wait,1200)
        self.s['metrics']['floor']['values']=[2]
        self.pace.apply(self.decision,self.s,self.choice,True)
        self.assertEqual(self.pace.wait,600)
        self.pace=AdaptivePacing(200)
        self.assertEqual(self.sample()['next_wait_ms'],200)


class OCRCacheTests(TestCase):
    def reader(self):
        ocr=ScreenOCR('windows.media.ocr')
        ocr.read_engine=Mock(side_effect=lambda png,engine:{'status':'ok','frame_sha256':png.hex(),'lines':[{'text':png.hex()}]})
        return ocr

    def test_interleaved_identical_images_reuse_ocr_and_changed_bytes_do_not(self):
        ocr=self.reader();a=ocr._read(b'A');ocr._read(b'B')
        cached=ocr._read(b'A');self.assertEqual(ocr.read_engine.call_count,2)
        self.assertTrue(cached['cache_hit']);self.assertEqual(cached['lines'],a['lines'])
        cached['lines'][0]['text']='external mutation'
        self.assertEqual(ocr._read(b'A')['lines'],a['lines'])
        ocr._read(b'C');self.assertEqual(ocr.read_engine.call_count,3)

    def test_failed_reads_retry_and_cache_is_bounded(self):
        ocr=self.reader();ocr.read_engine=Mock(return_value={'status':'unavailable','lines':[]})
        ocr._read(b'A');ocr._read(b'A');self.assertEqual(ocr.read_engine.call_count,2)
        ocr=self.reader()
        for i in range(20):ocr._read(bytes([i]))
        self.assertEqual(len(ocr.cache),16)
        ocr._read(bytes([0]));self.assertEqual(ocr.read_engine.call_count,21)

    def test_engine_configuration_change_does_not_reuse_other_engine_results(self):
        ocr=self.reader();ocr._read(b'A');ocr.selected='ocr-paddle-v5';ocr._read(b'A')
        self.assertEqual(ocr.read_engine.call_count,2)
