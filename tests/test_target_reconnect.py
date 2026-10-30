"""Lifecycle contracts only. No game process is started by these tests."""
import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from autoplayer.recovery import TargetRecovery,TargetUnavailable
from autoplayer.session import SessionControl

class ReconnectContracts(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.exe=self.root/'body.exe';self.exe.write_bytes(b'not executed')
        self.target={'executable':str(self.exe),'pid':12,'hwnd':22,'class_name':'Game','title':'Selected game'}
        self.control=SessionControl();self.events=[]
    def tearDown(self):self.control.close();self.tmp.cleanup()
    def recover(self,scans,child_pid=80,processes=lambda:[]):
        windows=iter(scans);launch=Mock(return_value=SimpleNamespace(pid=child_pid,poll=lambda:0))
        self.control.wait=lambda _:False
        r=TargetRecovery(self.target,self.events.append,windows=lambda:next(windows),alive=lambda _:False,launch=launch,processes=processes,clock=lambda:0)
        return r,launch
    def test_launcher_exit_and_different_body_pid_with_same_executable(self):
        game={**self.target,'pid':99,'hwnd':88}
        r,launch=self.recover([[],[game],[game]])
        self.assertEqual(r.recover(self.control)['pid'],99);self.assertEqual(launch.call_count,1)
    def test_descendant_body_at_different_path_is_attached(self):
        sub=self.root/'bin';sub.mkdir();body=sub/'shipping.exe';body.write_bytes(b'not executed')
        game={**self.target,'executable':str(body),'pid':99,'hwnd':88}
        r,launch=self.recover([[],[game],[game]],processes=lambda:[{'pid':99,'parent_pid':80}])
        self.assertEqual(r.recover(self.control)['executable'],str(body));launch.assert_called_once_with(str(self.exe))
    def test_existing_unrelated_same_app_is_not_adopted(self):
        other={**self.target,'pid':50,'hwnd':55};launch=Mock();self.control.wait=lambda _:self.control.stop.set()
        r=TargetRecovery(self.target,self.events.append,windows=lambda:[other],alive=lambda _:False,launch=launch,processes=lambda:[])
        self.assertIsNone(r.recover(self.control));launch.assert_not_called()
    def test_entry_is_distinct_from_game_and_saved_across_reconnect(self):
        entry=self.root/'launcher.exe';entry.write_bytes(b'not executed');self.target['launch_executable']=str(entry)
        game={**self.target,'pid':99,'hwnd':88};r,launch=self.recover([[],[game],[game]])
        found=r.recover(self.control);launch.assert_called_once_with(str(entry));self.assertEqual(found['launch_executable'],str(entry))
    def test_parent_launch_entry_learned_from_same_application_directory(self):
        entry=self.root/'launcher.exe';entry.write_bytes(b'not executed')
        r=TargetRecovery(self.target,self.events.append,windows=lambda:[],processes=lambda:[{'pid':12,'parent_pid':3}],executable_for_pid=lambda _:str(entry))
        self.assertEqual(r.entry,str(entry))
    def test_active_connect_button_only_signals_the_owning_worker(self):
        from autoplayer.ui import Application
        app=SimpleNamespace(busy=lambda:True,control=self.control,status=Mock(),launch=Mock())
        Application.connect(app);self.assertTrue(self.control.reconnect_requested.is_set());self.assertFalse(self.control.stop.is_set());app.launch.assert_not_called()
    def test_reconnect_cancels_stale_controller_input_without_stopping_session(self):
        from autoplayer.nextgen.controller import NextGenController
        runner=NextGenController.__new__(NextGenController);runner.control=self.control;runner.stop=self.control.stop
        self.control.request_reconnect()
        with self.assertRaises(TargetUnavailable):runner.checkpoint()
        self.assertFalse(self.control.stop.is_set());self.control.reconnect_requested.clear();self.assertTrue(runner.checkpoint())
    def test_stop_after_scan_does_not_attach_or_launch(self):
        self.control.stop.set();r,launch=self.recover([])
        self.assertIsNone(r.recover(self.control));launch.assert_not_called()
