"""Addressed key lifetime and observation-mode contracts; no simulated game."""
import threading
import unittest
from unittest.mock import Mock
from autoplayer.windows import WindowsGame

class MapDisplayKeyTests(unittest.TestCase):
    def backend(self):
        backend=WindowsGame.__new__(WindowsGame);backend.title='torneko';backend.held=set();backend.input_target=None
        backend.capture_lock=threading.RLock();backend.ready=Mock();backend._input_window=Mock(return_value=101)
        backend._send=Mock(side_effect=lambda vk,up=False:{'os_submission':'accepted','message':'WM_KEYUP' if up else 'WM_KEYDOWN','vk':vk})
        return backend
    def test_images_are_captured_while_held_and_key_released_before_return(self):
        backend=self.backend();held=[]
        def capture():held.append(0x67 in backend.held);return b'capture-contract'
        backend.capture=Mock(side_effect=capture);stop=Mock();stop.is_set.return_value=False;stop.wait.return_value=False
        result=backend.capture_map_display(stop,baseline=True)
        self.assertEqual(held,[True,True,False]);self.assertFalse(backend.held)
        self.assertEqual([r['message'] for r in result['events']],['WM_KEYDOWN','WM_KEYUP'])
        self.assertEqual(result['kind'],'held_map_display_not_keyless');self.assertFalse(result['learning'])
    def test_capture_failure_releases_key(self):
        backend=self.backend();backend.capture=Mock(side_effect=RuntimeError('capture failed'))
        stop=Mock();stop.is_set.return_value=False;stop.wait.return_value=False
        with self.assertRaisesRegex(RuntimeError,'capture failed'):backend.capture_map_display(stop)
        self.assertFalse(backend.held);self.assertTrue(backend._send.call_args.args[1])
    def test_pause_or_stop_releases_key_without_taking_a_stale_sample(self):
        backend=self.backend();backend.capture=Mock();stop=Mock();stop.is_set.return_value=False;stop.wait.return_value=True
        self.assertEqual(backend.capture_map_display(stop)['status'],'cancelled')
        self.assertFalse(backend.held);backend.capture.assert_not_called()
    def test_stopped_before_keydown_sends_nothing(self):
        backend=self.backend();stop=Mock();stop.is_set.return_value=True
        self.assertEqual(backend.capture_map_display(stop)['status'],'cancelled');backend._send.assert_not_called()

class MapDisplayPolicyTests(unittest.TestCase):
    def test_normal_and_held_display_are_not_compared_as_progress(self):
        import tempfile
        from pathlib import Path
        from autoplayer.core import Settings
        from autoplayer.memory import MemoryStore
        from autoplayer.policy import ExperiencePolicy
        from test_policy import state,receipt_row
        with tempfile.TemporaryDirectory() as folder:
            store=MemoryStore(Path(folder)/'memory.sqlite',Settings());self.addCleanup(store.close)
            policy=ExperiencePolicy(store,Settings());before=state();after=state('B',261.7)
            after['observation_mode']='num7_held'
            self.assertEqual(policy.learn(before,after,receipt_row())['status'],'censored')
            before['observation_mode']='num7_held'
            result=policy.learn(before,after,receipt_row(policy_baseline_kind='held_map_display_not_keyless'),before)
            self.assertEqual(result['status'],'trained');self.assertEqual(result['baseline_kind'],'held_map_display_not_keyless')
    def test_missing_map_does_not_trigger_a_display_input(self):
        import tempfile
        from pathlib import Path
        from autoplayer.core import Controller,Settings
        from autoplayer.memory import MemoryStore
        from test_agent import Backend,Script
        from test_policy import state
        with tempfile.TemporaryDirectory() as folder:
            store=MemoryStore(Path(folder)/'memory.sqlite',Settings());self.addCleanup(store.close)
            backend=Backend();backend.capture_map_display=Mock()
            agent=Controller(Settings(),backend,Script([]),lambda row:None,Path(folder)/'run',memory=store)
            unknown=state();unknown['minimap']={'status':'unavailable'}
            self.assertIsNone(agent.map_sample(b'frame',unknown,'map_observation'))
            menu=state();menu['menu_evidence']=True
            self.assertIsNone(agent.map_sample(b'frame',menu,'map_observation'))
            backend.capture_map_display.assert_not_called()

    def test_controller_records_display_receipts_separately_from_gameplay(self):
        import tempfile,json
        from pathlib import Path
        from types import SimpleNamespace
        from autoplayer.core import Controller,Settings
        from autoplayer.memory import MemoryStore
        from test_agent import Backend,Script
        from test_policy import state
        with tempfile.TemporaryDirectory() as folder:
            store=MemoryStore(Path(folder)/'memory.sqlite',Settings());self.addCleanup(store.close)
            backend=Backend();backend.record_frame=Mock()
            backend.capture_map_display=Mock(return_value={'status':'captured','image':b'held-current','baseline_image':b'held-baseline',
                'restored_image':b'restored','events':[{'message':'WM_KEYDOWN'},{'message':'WM_KEYUP'}],'learning':False})
            llm=Script([]);llm.ocr=SimpleNamespace(read=Mock(return_value={'status':'ok','lines':[]}))
            agent=Controller(Settings(),backend,llm,lambda row:None,Path(folder)/'run',memory=store)
            agent.policy.observe=Mock(side_effect=[state(),state()])
            current,baseline,restored=agent.map_sample(b'normal',state(),'map_observation',baseline=True)
            self.assertEqual(current['observation_mode'],'num7_held');self.assertEqual(baseline['observation_mode'],'num7_held')
            self.assertEqual(restored,b'restored')
            self.assertTrue(all(call.kwargs['map_display'] for call in agent.policy.observe.call_args_list))
            rows=[json.loads(line) for line in agent.journal.read_text().splitlines()]
            row=next(r for r in rows if r['event']=='map_observation');self.assertFalse(row['learning'])
            self.assertEqual(rows[0]['event'],'map_observation_requested')
            self.assertEqual(len([r for r in rows if r['event']=='map_reading']),2)
            self.assertNotIn('image',row);self.assertEqual(backend.actions,[])
