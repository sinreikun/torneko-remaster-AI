"""Configuration, persistence and lifecycle contracts; no game simulation."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from autoplayer.core import Settings, Decision, Controller, LocalLLM
from autoplayer.profiles import ProfileStore, parse_bindings
from autoplayer.memory import MemoryStore
from autoplayer.adaptation import DurationLearner
from autoplayer.interfaces import choice_action
from autoplayer.recovery import TargetRecovery, TargetUnavailable
from autoplayer.windows import WindowsGame
from autoplayer.session import SessionControl


class ProfileTests(unittest.TestCase):
    def test_title_pid_and_handle_changes_do_not_change_game_identity(self):
        target={'title':'Game - level 1','executable':'C:/Games/Game.exe','class_name':'Game','pid':5,'hwnd':10}
        changed={**target,'title':'Game - level 2','pid':6,'hwnd':20}
        self.assertEqual(ProfileStore.identity(target),ProfileStore.identity(changed))
        self.assertNotEqual(ProfileStore.identity(target),ProfileStore.identity({**target,'executable':'C:/Other/Game.exe'}))

    def test_different_games_in_a_shared_host_have_separate_profiles(self):
        target={'title':'First game','executable':'C:\\Browser\\chrome.exe','class_name':'Browser'}
        self.assertNotEqual(ProfileStore.identity(target),ProfileStore.identity({**target,'title':'Second game'}))

    def test_other_game_does_not_inherit_torneko_knowledge_or_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=ProfileStore(tmp);target={'title':'Other game','executable':'C:/Other.exe','class_name':'Game'}
            cfg=store.select(target,Settings(knowledge='トルネコ専用'))
            self.assertEqual(cfg.knowledge,'');self.assertNotIn('num7',cfg.allowed_keys)
            self.assertTrue(cfg.resilient);self.assertTrue(cfg.adaptive_timing)
            cfg.key_bindings={'q':'左移動','e':'右移動'};cfg.allowed_keys=list(cfg.key_bindings)
            store.save(target,cfg)
            loaded=store.select({**target,'title':'Other game - title changed'},Settings())
            self.assertEqual(loaded.key_bindings,cfg.key_bindings)
            self.assertEqual(loaded.profile_id,cfg.profile_id)
            loaded.validate()

    def test_profile_memory_survives_goal_and_title_edits_but_isolates_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'memory.sqlite';cfg=Settings(window_title='Other',profile_id='a'*24,allowed_keys=['q'],key_bindings={'q':'前進'})
            first=MemoryStore(path,cfg);identity=first.profile
            first.remember({'scene':'gameplay','ocr':{'lines':[{'text':'扉を開く'}]},'executed':True,'observed_keys':['q']})
            first.close();cfg.window_title='Changed';cfg.goal='新しい目標'
            second=MemoryStore(path,cfg);self.assertEqual(second.profile,identity)
            self.assertEqual(len(second.recall({'lines':[{'text':'扉'}]},cfg.goal)),1);second.close()
            cfg.key_bindings={'q':'キャンセル'}
            third=MemoryStore(path,cfg);self.assertNotEqual(third.profile,identity);self.assertEqual(third.history(),[]);third.close()

    def test_bindings_reject_executable_or_shortcut_keys(self):
        bindings=parse_bindings('w=前進\nnum1=決定')
        self.assertEqual(bindings['w'],'前進')
        with self.assertRaises(ValueError):parse_bindings('前進')
        with self.assertRaises(ValueError):Settings(window_title='Other',allowed_keys=['alt'],key_bindings={'alt':'操作'}).validate()


class DurationTests(unittest.TestCase):
    def test_only_actual_accepted_comparable_inputs_train_and_do_not_claim_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Settings(window_title='Other',allowed_keys=['w'],key_bindings={'w':'前進'},adaptive_timing=True)
            memory=MemoryStore(Path(tmp)/'memory.sqlite',cfg)
            learner=DurationLearner(memory,cfg)
            decision=Decision('gameplay','現在の観察','通路を進む','進む',.9,'continue',['w'],80,0)
            trial=learner.choose(decision);self.assertEqual(trial['ms'],40)
            row={'executed':True,'observed_keys':['w'],'raw_change':.5,'turn_kind':'normal',
                 'neutral_before':{'raw_change':.01,'kind':'no_change'},'input_receipt':{'os_submission':'accepted'}}
            for modified in ({'dry_run':True},{'turn_kind':'aborted'},{'executed':False},{'input_receipt':{'os_submission':'rejected'}},{'observed_keys':[]},{'neutral_before':{'status':'not_sampled'}}):
                learner.record(trial,{**row,**modified})
            self.assertEqual(learner.context()['duration_trials'],[])
            learner.record(trial,row)
            self.assertEqual(learner.context()['duration_trials'][0]['trials'],1)
            self.assertEqual(learner.choose(decision)['ms'],80)
            decision.scene='dialog';self.assertIsNone(learner.choose(decision))
            memory.close()

    def test_actual_neutral_sample_advances_unconfirmed_duration_without_failure_learning(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Settings(window_title='Other',allowed_keys=['w'],key_bindings={'w':'上移動'})
            memory=MemoryStore(Path(tmp)/'memory.sqlite',cfg);learner=DurationLearner(memory,cfg)
            d=Decision('gameplay','観察','進む','理由',.9,'continue',['w'],80,0)
            trial=learner.choose(d)
            row={'executed':True,'observed_keys':['w'],'input_receipt':{'os_submission':'accepted'},
                'neutral_before':{'kind':'neutral_sample','raw_change':0},'operation':{'skill':'explore'},
                'action_effect_learning':{'effects':{'player_delta':[0,0]}}}
            learner.record(trial,row)
            self.assertEqual(learner.context()['duration_trials'],[])
            next_trial=learner.choose(d);self.assertEqual(next_trial['ms'],80)
            learner.record(next_trial,{**row,'action_effect_learning':{'effects':{'player_delta':[0,-1]}}})
            evidence=learner.context()['duration_trials'][0]
            self.assertEqual(evidence['response_associations'],1)
            self.assertEqual(evidence['ms'],80)
            memory.close()

    def test_natural_activity_is_not_a_positive_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Settings(window_title='Other',allowed_keys=['w'],key_bindings={'w':'上移動'})
            memory=MemoryStore(Path(tmp)/'memory.sqlite',cfg);learner=DurationLearner(memory,cfg)
            d=Decision('gameplay','観察','進む','理由',.9,'continue',['w'],80,0);trial=learner.choose(d)
            learner.record(trial,{'executed':True,'observed_keys':['w'],'input_receipt':{'os_submission':'accepted'},
                                 'neutral_before':{'kind':'timeout','raw_change':.5},'raw_change':.6})
            self.assertEqual(learner.context()['duration_trials'][0]['response_associations'],0);memory.close()


class InterfaceTests(unittest.TestCase):
    bindings={'num1':'決定','w':'上移動','s':'下移動','a':'左移動','d':'右移動'}
    def test_explicit_selected_yes_confirms_without_model(self):
        action=choice_action({'status':'ok','lines':[{'text':'▶ は い'}]},self.bindings,list(self.bindings))
        self.assertEqual(action['key'],'num1')

    def test_unselected_yes_moves_then_requires_another_observation(self):
        native={'status':'ok','lines':[{'text':'はい','words':[{'box':[100,20,20,10]}]},
                                     {'text':'▶いいえ','words':[{'box':[100,50,40,10]}]}]}
        self.assertEqual(choice_action(native,self.bindings,list(self.bindings))['key'],'w')

    def test_unmarked_or_destructive_or_body_text_is_not_a_choice(self):
        for labels in (['はい','いいえ'],['▶はい','削除しますか'],['「はい」を選んでください'],['▶いいえ']):
            self.assertIsNone(choice_action({'status':'ok','lines':[{'text':t} for t in labels]},self.bindings,list(self.bindings)))

    def test_frame_linked_choice_bypasses_model_but_stale_ocr_does_not(self):
        cfg=Settings(window_title='Other',profile_id='a'*24,allowed_keys=list(self.bindings),key_bindings=self.bindings)
        llm=LocalLLM(cfg);frame=b'frame-identity-only'
        native={'status':'ok','frame_sha256':hashlib.sha256(frame).hexdigest(),'lines':[{'text':'▶はい'}]}
        llm.ocr=Mock();llm.ocr.read.return_value=native
        llm._request=Mock(side_effect=AssertionError('Unexpected inference'))
        self.assertEqual(llm.decide(frame,[],False).keys,['num1']);llm._request.assert_not_called()
        llm.ocr.read.return_value={**native,'frame_sha256':'stale'}
        llm.observe_scene=Mock(side_effect=ValueError('Observation unavailable'))
        self.assertEqual(llm.decide(frame,[],False).keys,[]);llm.observe_scene.assert_called_once()


class RecoveryTests(unittest.TestCase):
    def target(self,path):return {'title':'Game','executable':str(path),'class_name':'Game','pid':12,'hwnd':22}

    def test_existing_process_is_not_relaunched_and_stop_interrupts_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'game.exe';path.write_bytes(b'not executed');events=[];launch=Mock();control=SessionControl()
            control.wait=lambda _:control.stop.set()
            recovery=TargetRecovery(self.target(path),events.append,windows=lambda:[],alive=lambda _:True,launch=launch)
            self.assertIsNone(recovery.recover(control));launch.assert_not_called();control.close()

    def test_closed_process_launches_once_then_attaches_new_handle_without_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'game.exe';path.write_bytes(b'not executed');target=self.target(path);new={**target,'pid':99,'hwnd':88}
            scans=iter([[],[],[new]]);events=[];launch=Mock(return_value=SimpleNamespace(pid=99,poll=lambda:None));control=SessionControl()
            control.wait=lambda _:False
            recovery=TargetRecovery(target,events.append,windows=lambda:next(scans),alive=lambda pid:pid==99,launch=launch)
            self.assertEqual(recovery.recover(control),new);launch.assert_called_once_with(str(path))
            self.assertTrue(all(e['learning'] is False for e in events));control.close()

    def test_multiple_matches_and_unknown_executable_never_launch(self):
        control=SessionControl();control.wait=lambda _:control.stop.set();launch=Mock()
        target={'title':'Game','executable':'','class_name':'Game','pid':12}
        self.assertIsNone(TargetRecovery(target,lambda _:None,windows=lambda:[],alive=lambda _:False,launch=launch).recover(control))
        launch.assert_not_called();control.close()

    def test_does_not_take_over_another_instance_with_same_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'game.exe';path.write_bytes(b'not executed');target=self.target(path)
            other={**target,'pid':100,'hwnd':300};control=SessionControl();launch=Mock()
            control.wait=lambda _:control.stop.set()
            recovery=TargetRecovery(target,lambda _:None,windows=lambda:[other],alive=lambda _:False,launch=launch)
            self.assertIsNone(recovery.recover(control));launch.assert_not_called();control.close()

    def test_stop_arriving_during_process_scan_prevents_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'game.exe';path.write_bytes(b'not executed');control=SessionControl();launch=Mock()
            def windows():control.stop.set();return []
            recovery=TargetRecovery(self.target(path),lambda _:None,windows=windows,alive=lambda _:False,launch=launch)
            self.assertIsNone(recovery.recover(control));launch.assert_not_called();control.close()

    def test_pause_and_stop_prevent_launch(self):
        control=SessionControl();control.toggle_pause();control.stop.set();launch=Mock()
        recovery=TargetRecovery({'executable':'x.exe'},lambda _:None,windows=Mock(),launch=launch)
        self.assertIsNone(recovery.recover(control));recovery.windows.assert_not_called();launch.assert_not_called();control.close()

    def test_closed_window_key_release_does_not_send_to_another_process(self):
        backend=WindowsGame.__new__(WindowsGame);backend.hwnd=5;backend.input_target=6;backend.held={65}
        backend.u=SimpleNamespace(IsWindow=lambda _:False);backend._send=Mock()
        self.assertEqual(backend.release_all()[0]['os_submission'],'not_attempted');backend._send.assert_not_called()
        self.assertFalse(backend.held)

    def test_duplicate_titles_use_explicit_handle_and_never_guess_another_instance(self):
        from autoplayer.ui import Application
        targets=[{'title':'Same','pid':1,'hwnd':10,'executable':'A.exe','class_name':'Game'},
                 {'title':'Same','pid':2,'hwnd':20,'executable':'A.exe','class_name':'Game'}]
        with patch('autoplayer.windows.window_targets',return_value=targets),patch('autoplayer.windows.WindowsGame') as constructor:
            Application.open_target('Same',targets[1]);constructor.assert_called_once_with('Same',hwnd=20)
        with patch('autoplayer.windows.window_targets',return_value=targets),patch('autoplayer.windows.WindowsGame') as constructor:
            with self.assertRaises(TargetUnavailable):Application.open_target('Same',{**targets[1],'pid':3,'hwnd':30})
            constructor.assert_not_called()


if __name__=='__main__':unittest.main()
