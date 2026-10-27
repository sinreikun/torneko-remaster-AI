"""Exact-frame OCR, static pixel bands and dispatch timing contracts; no game."""
import copy,hashlib,io,threading,unittest,tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from PIL import Image
from autoplayer.observation_store import ObservationStore,message_candidates
from autoplayer.observation import TurnObserver
from autoplayer.core import Controller,Settings
from autoplayer.pacing import AdaptivePacing
from autoplayer.supervision import Supervisor
from test_policy import state,receipt_row
from test_supervision import ledger,proposal,reply_ready


def png(color):
    out=io.BytesIO();Image.new('RGB',(128,96),color).save(out,format='PNG');return out.getvalue()
def reading(data,lines):return {'status':'ok','frame_sha256':hashlib.sha256(data).hexdigest(),'lines':[{'text':t} for t in lines]}

class SharedObservationTests(unittest.TestCase):
    def test_same_frame_mode_and_turn_share_independent_mutable_copies(self):
        store=ObservationStore();store.begin(1);frame=b'exact';produce=Mock(return_value=reading(frame,['HP4/15']))
        one=store.read(frame,'full',produce);one['lines'].clear()
        self.assertEqual(len(store.read(frame,'full',produce)['lines']),1);self.assertEqual(produce.call_count,1)
        store.read(frame,'fast',produce);store.read(frame,'full',produce,independent=True)
        self.assertEqual(produce.call_count,3)
        store.begin(2);store.read(frame,'full',produce);self.assertEqual(produce.call_count,4)
    def test_failures_and_mismatched_frames_are_not_shared(self):
        store=ObservationStore();produce=Mock(return_value={'status':'unavailable'})
        for _ in range(2):store.read(b'frame','full',produce)
        self.assertEqual(produce.call_count,2)
        produce.return_value=reading(b'other',[])
        for _ in range(2):store.read(b'frame','full',produce)
        self.assertEqual(produce.call_count,4)
    def test_old_frame_candidates_are_bounded_deduplicated_and_message_only(self):
        black=png('black');gray=png('gray');white=png('white')
        selected=message_candidates(black,[black,gray,white,white],limit=1)
        self.assertEqual(selected,[white]);self.assertEqual(message_candidates(b'not PNG',[white]),[])
    def test_battle_retry_recovers_messages_without_replacing_current_hp(self):
        final=png('black');old=png('white');agent=Controller.__new__(Controller)
        agent.cfg=Settings();agent.policy=None;agent.current_step=1;agent.journal=Path('session.jsonl')
        agent.llm=SimpleNamespace(ocr=SimpleNamespace(read=Mock(side_effect=lambda b:reading(b,['HP1/15'] if b==final else ['HP4/15','2ポイントのダメージを与えた。','ゴーストから3ポイントのダメージを受けた。']))))
        row=receipt_row('num1');before=state();before['metrics']['hp']['values']=[4,15]
        result=agent.read_action_result_ocr(final,row,before,[old])
        self.assertEqual(result['lines'][0]['text'],'HP1/15')
        self.assertEqual(row['combat_message_ocr']['text_status'],'text_read_or_no_text_required')
        self.assertEqual(row['combat_message_ocr']['retry_count'],1)
        self.assertEqual(row['combat_message_samples'][0]['role'],'message_only_within_current_input')
        self.assertEqual(row['combat_message_samples'][0]['frame_sha256'],hashlib.sha256(old).hexdigest())
    def test_unexpected_hp_drop_escalates_to_fresh_full_result_read(self):
        frame=png('gray');flags=[];agent=Controller.__new__(Controller)
        agent.cfg=Settings(fast_learning=True);agent.policy=None;agent.current_step=1;agent.journal=Path('session.jsonl')
        ocr=SimpleNamespace(force_fresh=False,fast_mode=False)
        def read(data):
            flags.append(ocr.force_fresh)
            return reading(data,['HP1/15','ゴーストから3ポイントのダメージを受けた。'])
        ocr.read=read;agent.llm=SimpleNamespace(ocr=ocr)
        before=state();before['metrics']['hp']['values']=[4,15]
        row=receipt_row('w');agent.read_action_result_ocr(frame,row,before)
        self.assertEqual(flags,[False,True]);self.assertTrue(row['combat_message_ocr']['frame_linked'])

    def test_text_protection_keeps_requested_wait_and_bounded_frames(self):
        class Clock:
            now=0.
            def __call__(self):return self.now
            def wait(self,d):self.now+=d;return False
            def is_set(self):return False
        o=TurnObserver();o.fast_mode=True;o.grid=lambda value:value;o.input_signature=(('num1',),0)
        c=Clock()
        for _ in range(5):o.observe(lambda:[255]*768,[0]*768,'gameplay',c,800,clock=c)
        o.capture_evidence=True;o.protect_text=True
        result=o.observe(lambda:[255]*768,[0]*768,'gameplay',c,800,clock=c)
        self.assertEqual(result.applied_minimum_ms,800);self.assertEqual(len(result.evidence_frames),1)
        o.protect_text=False;o.external_timing=True
        result=o.observe(lambda:[255]*768,[0]*768,'gameplay',c,600,clock=c)
        self.assertEqual(result.applied_minimum_ms,600);self.assertEqual(result.timing_mode,'policy_margin')
    def test_floor_route_and_diagonal_have_calibrated_wait_and_battle_does_not(self):
        pace=AdaptivePacing(5000);s=state();d=SimpleNamespace(keys=['w','d'],risk='normal',wait_ms=600)
        trial=pace.apply(d,s,{'source':'floor_navigation'},True);self.assertIsNotNone(trial)
        d.keys=['num1'];self.assertIsNone(pace.apply(d,s,{'source':'combat_experiment'},True))
    def test_retry_and_loop_screenshots_have_priority_storage_and_explicit_role(self):
        from autoplayer.important_events import ImportantEvents
        import json
        with tempfile.TemporaryDirectory() as tmp:
            events=ImportantEvents(tmp)
            row={'step':1,'combat_evidence_frame_role':'message_sample_within_input_not_current_state','combat_message_ocr':{'text_status':'text_read_or_no_text_required'}}
            events.observe(row,png('black'),png('white'))
            saved=json.loads(next(Path(tmp).rglob('event.json')).read_text())
            self.assertIn('battle_text_recovered',saved['reasons'])
            self.assertEqual(saved['after_evidence_role'],'message_sample_within_input_not_current_state')
            events.observe({'floor_exploration':{'cycle_status':{'detected':True}}},png('black'),png('black'))
            all_events=[json.loads(p.read_text()) for p in Path(tmp).rglob('event.json')]
            self.assertTrue(any('navigation_cycle' in e['reasons'] for e in all_events))

    def test_fast_review_interval_is_not_overridden_by_local_plan_expiry(self):
        sup=Supervisor(lambda *a:proposal(),threading.Event(),review_interval=64);self.addCleanup(sup.close)
        l=ledger();sup.tick(l);reply_ready(sup);sup.tick(l)
        self.assertEqual(sup.adoptions,1);scheduled=sup.next_review
        l=ledger(step=17);sup.tick(l)
        self.assertEqual(sup.requests,1);self.assertEqual(sup.next_review,scheduled)
