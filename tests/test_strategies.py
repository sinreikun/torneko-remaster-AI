"""Method scheduling, evidence boundaries and HTTP contracts; no game renderer."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from autoplayer.core import Settings,LocalLLM,Controller,parse_decision
from autoplayer.memory import MemoryStore
from autoplayer.strategies import MethodLearner,progress_signal,METHODS
from autoplayer.background import BackgroundEvidence,BackgroundUnavailable
from autoplayer.windows import WindowsGame
from autoplayer.research import public_references,reference_context
from test_agent import VALID,OBSERVED


def ocr(*lines):return {'status':'ok','lines':[{'text':s} for s in lines]}


class FrozenCriterionTests(unittest.TestCase):
    def test_already_present_text_and_animation_are_not_progress(self):
        criterion={'kind':'text_appears','text':'通過しました'}
        self.assertFalse(progress_signal(criterion,ocr('通過しました'),ocr('通過しました')))
        self.assertFalse(progress_signal(criterion,ocr('HP15/15'),ocr('HP15/15')))
        self.assertTrue(progress_signal(criterion,ocr('扉の前'),ocr('通 過 し ま し た')))

    def test_missing_ocr_is_not_disappearance_or_failure(self):
        criterion={'kind':'text_disappears','text':'鍵が必要'}
        self.assertIsNone(progress_signal(criterion,ocr('鍵が必要'),{'status':'unavailable'}))
        self.assertIsNone(progress_signal(criterion,ocr('鍵が必要'),ocr()))
        self.assertTrue(progress_signal(criterion,ocr('鍵が必要'),ocr('扉が開いた')))

    def test_invalid_rule_does_not_become_an_executable_expression(self):
        self.assertIsNone(progress_signal({'kind':'python','text':'print(1)'},ocr('前'),ocr('後')))
        with self.assertRaises(ValueError):
            parse_decision(json.dumps({**VALID,'progress_test':{'kind':'shell','text':'anything'}}),Settings())


class MethodTests(unittest.TestCase):
    def store(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        cfg=Settings(window_title='Other',profile_id='c'*24)
        memory=MemoryStore(Path(temporary.name)/'memory.sqlite',cfg);self.addCleanup(memory.close)
        return cfg,memory,MethodLearner(memory)

    def test_no_progress_switches_methods_without_treating_it_as_backend_failure(self):
        cfg,memory,learner=self.store()
        selection=learner.select(OBSERVED,has_history=True);self.assertEqual(selection['method'],'grounded_text')
        for _ in range(3):learner.record(selection,True,elapsed_ms=100,signal=None)
        replacement=learner.select(OBSERVED,has_history=True)
        self.assertNotEqual(replacement['method'],selection['method']);self.assertTrue(replacement['switched'])
        self.assertEqual(sum(r['criterion_observations'] for r in learner.context()['methods']),0)

    def test_trials_and_unverified_genre_persist_and_are_game_specific(self):
        cfg,memory,learner=self.store()
        observation={**OBSERVED,'game_hypothesis':{'genre':'puzzle','tempo':'turn_based','objectives':['出口へ進む'],'evidence':['盤面が見える']}}
        selection=learner.select(observation);learner.record(selection,True,signal=True)
        reopened=MethodLearner(memory)
        self.assertEqual(reopened.context()['game_hypothesis']['status'],'model_hypothesis_unverified')
        self.assertEqual(reopened.context()['methods'][0]['criterion_observations'],1)
        cfg.profile_id='d'*24;other=MemoryStore(Path(memory.db.execute('PRAGMA database_list').fetchone()[2]),cfg)
        try:self.assertEqual(MethodLearner(other).context()['methods'],[])
        finally:other.close()

    def test_dry_run_and_censored_results_do_not_train(self):
        _,_,learner=self.store();learner.read_only=True
        selection=learner.select(OBSERVED);learner.record(selection,True,signal=True)
        self.assertEqual(learner.context()['methods'],[])
        learner.read_only=False;learner.record(selection,True,signal=True,eligible=False)
        self.assertEqual(learner.context()['methods'],[])

    def test_visual_replanning_passes_current_image_and_retains_output_limits(self):
        cfg,_,learner=self.store();cfg.allowed_keys=['num1']
        llm=LocalLLM(cfg);llm.methods=learner;llm.ocr=Mock();llm.ocr.read.return_value=ocr('開始')
        llm.observe_scene=Mock(return_value=OBSERVED);requests=[]
        learner.select=lambda *a,**k:{'method':'visual_replan','scene_bucket':'title:unknown','game_hypothesis':{'genre':'unknown'}}
        def request(image,system,prompt,schema=None):
            requests.append((image,prompt,schema));return json.dumps(VALID,ensure_ascii=False)
        llm._request=request
        decision=llm.decide(b'current-frame-identity',[],False)
        self.assertEqual(requests[0][0],b'current-frame-identity')
        self.assertEqual(decision.keys,['num1']);self.assertEqual(requests[0][1]['allowed_keys'],['num1'])

    def test_scene_contradiction_never_executes_a_menu_action(self):
        cfg,_,learner=self.store();llm=LocalLLM(cfg);llm.methods=learner
        llm.ocr=Mock();llm.ocr.read.return_value=ocr('HP15/15')
        llm.observe_scene=Mock(return_value={**OBSERVED,'scene_type':'gameplay'})
        llm._request=Mock(return_value=json.dumps(VALID,ensure_ascii=False))
        self.assertEqual(llm.decide(b'identity',[],False).keys,[])
        self.assertEqual(learner.context()['methods'][0]['valid'],0)

    def test_controller_credits_only_frame_linked_criteria_above_neutral_activity(self):
        import hashlib
        for neutral_change,matched,expected in ((0,True,1),(.5,True,0),(0,False,0)):
            with self.subTest(neutral=neutral_change,matched=matched),tempfile.TemporaryDirectory() as tmp:
                cfg=Settings(window_title='Other',profile_id='e'*24,allowed_keys=['num1'],max_steps=1)
                memory=MemoryStore(Path(tmp)/'memory.sqlite',cfg)
                first={**ocr('扉の前'),'frame_sha256':hashlib.sha256(b'before').hexdigest()}
                last={**ocr('扉が開いた'),'frame_sha256':hashlib.sha256(b'after').hexdigest() if matched else 'stale'}
                llm=SimpleNamespace(ocr=Mock());llm.ocr.read.return_value=last
                proposal={**VALID,'progress_test':{'kind':'text_appears','text':'扉が開いた'}}
                llm.decide=lambda *_:parse_decision(json.dumps(proposal),cfg)
                llm.take_state=lambda:{'ocr':first,'inference_metrics':[], 'method_selection':llm.methods.select(OBSERVED)}
                backend=SimpleNamespace(capture=lambda:b'before',release_all=lambda:None,
                    act=lambda *_:{'os_submission':'accepted','events':[{'message':'WM_KEYDOWN'}]})
                backend.observe_turn=Mock(side_effect=[
                    SimpleNamespace(image=b'before',kind='no_change',changed=False,raw_change=neutral_change,duration_ms=100,masked_activity=0),
                    SimpleNamespace(image=b'after',kind='normal',changed=True,raw_change=.2,duration_ms=100,masked_activity=0)])
                try:
                    Controller(cfg,backend,llm,lambda _:None,Path(tmp)/'run.jsonl',memory=memory).run()
                    self.assertEqual(llm.methods.context()['methods'][0]['criterion_observations'],expected)
                finally:memory.close()


class BackgroundContractTests(unittest.TestCase):
    def test_another_window_of_the_same_game_process_is_not_background(self):
        backend=WindowsGame.__new__(WindowsGame);backend.process_id=SimpleNamespace(value=55)
        def process(hwnd,pointer):pointer._obj.value=55
        backend.u=SimpleNamespace(GetForegroundWindow=lambda:99,GetWindowThreadProcessId=process)
        self.assertFalse(backend.is_background())
        backend.u.GetForegroundWindow=lambda:0
        self.assertIsNone(backend.is_background())

    def test_os_acceptance_is_not_proof_of_a_background_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory=MemoryStore(Path(tmp)/'memory.sqlite',Settings());evidence=BackgroundEvidence(memory)
            row={'executed':True,'input_receipt':{'os_submission':'accepted','background_before':True,'background_after':True}}
            self.assertEqual(evidence.update(row,False)['status'],'response_unconfirmed')
            self.assertEqual(evidence.update(row,True)['status'],'response_observed_in_background')
            self.assertEqual(evidence.update({**row,'dry_run':True},True)['accepted_submissions'],2)
            memory.close()

    def test_rejected_window_message_never_calls_a_global_input_fallback(self):
        backend=WindowsGame.__new__(WindowsGame);backend.hwnd=3;backend.input_target=3
        backend.u=SimpleNamespace(MapVirtualKeyW=lambda *_:1,PostMessageW=lambda *_:False,SendInput=Mock(),SetForegroundWindow=Mock())
        with self.assertRaises(BackgroundUnavailable):backend._send(65)
        backend.u.SendInput.assert_not_called();backend.u.SetForegroundWindow.assert_not_called()


class ReferenceTests(unittest.TestCase):
    def test_bounded_public_candidates_cache_without_sending_game_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            document={'query':{'pages':[{'title':'Example game','extract':'公開情報の候補'}]}}
            response=Mock();response.read.return_value=json.dumps(document).encode()
            opened=Mock();opened.__enter__=Mock(return_value=response);opened.__exit__=Mock(return_value=False)
            opener=Mock();opener.open.return_value=opened
            first=public_references('Example','c'*24,tmp,threading.Event(),opener)
            self.assertEqual(first['status'],'candidates_found');self.assertEqual(opener.open.call_count,2)
            for call in opener.open.call_args_list:
                self.assertIsNone(call.args[0].data);self.assertTrue(call.args[0].full_url.startswith('https://'))
            self.assertEqual(reference_context(first)['sources'][0]['applicability'],'unverified_candidate')
            second=public_references('Example','c'*24,tmp,threading.Event(),opener)
            self.assertEqual(second,first);self.assertEqual(opener.open.call_count,2)

    def test_network_failure_or_stop_does_not_require_user_intervention(self):
        with tempfile.TemporaryDirectory() as tmp:
            opener=Mock();opener.open.side_effect=OSError('offline')
            value=public_references('Example','c'*24,tmp,threading.Event(),opener)
            self.assertEqual(value['sources'],[]);self.assertEqual(len(value['errors']),2)
            stop=threading.Event();stop.set();opener.reset_mock()
            self.assertEqual(public_references('Different','d'*24,tmp,stop,opener)['status'],'cancelled');opener.open.assert_not_called()


if __name__=='__main__':unittest.main()
