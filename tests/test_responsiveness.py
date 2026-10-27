"""Cancellation, IPC and UI callback contracts; no game or game renderer."""
import importlib
import queue
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from autoplayer.cancellation import OperationCancelled,call_interruptible,request_bytes,run_process
from autoplayer.capture_service import CaptureService,CaptureTimeout,capture_worker
from autoplayer.presentation import display_json
from autoplayer.ui import Application


class CancellationTests(unittest.TestCase):
    def test_stop_discards_a_late_result(self):
        entered=threading.Event();release=threading.Event();cancel=threading.Event();actions=[];errors=[]
        def operation():entered.set();release.wait(2);return 'late result'
        def caller():
            try:actions.append(call_interruptible(operation,cancel))
            except Exception as error:errors.append(error)
        thread=threading.Thread(target=caller,daemon=True);thread.start()
        try:
            self.assertTrue(entered.wait(1));cancel.set();thread.join(1)
            self.assertFalse(thread.is_alive());self.assertIsInstance(errors[0],OperationCancelled)
            release.set();self.assertEqual(actions,[])
        finally:release.set();thread.join(1)

    def test_stop_interrupts_waiting_for_http_headers(self):
        entered=threading.Event();release=threading.Event();cancel=threading.Event();errors=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length',0)))
                entered.set();release.wait(3)
                try:self.send_response(200);self.end_headers();self.wfile.write(b'{}')
                except (BrokenPipeError,ConnectionResetError):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        server_thread=threading.Thread(target=server.serve_forever,daemon=True);server_thread.start()
        def request():
            try:request_bytes(urllib.request.Request('http://127.0.0.1:'+str(server.server_port),data=b'{}'),600,cancel)
            except Exception as error:errors.append(error)
        thread=threading.Thread(target=request,daemon=True);thread.start()
        try:
            self.assertTrue(entered.wait(2));cancel.set();thread.join(1)
            self.assertFalse(thread.is_alive());self.assertIsInstance(errors[0],OperationCancelled)
        finally:
            release.set();server.shutdown();server.server_close();thread.join(1)

    def test_only_the_owned_ocr_process_is_killed_on_cancel(self):
        cancel=threading.Event();process=Mock();process.__enter__=Mock(return_value=process);process.__exit__=Mock(return_value=False)
        cancel.set()
        with patch('subprocess.Popen',return_value=process):
            with self.assertRaises(OperationCancelled):run_process(['owned-ocr-process'],25,cancel)
        process.kill.assert_called_once();process.communicate.assert_called_once()


class CaptureIsolationTests(unittest.TestCase):
    def service(self):
        alive={'value':False};parent=Mock();child=Mock();process=Mock()
        process.is_alive.side_effect=lambda:alive['value']
        process.start.side_effect=lambda:alive.update(value=True)
        process.terminate.side_effect=lambda:alive.update(value=False)
        context=Mock();context.Pipe.return_value=(parent,child);context.Process.return_value=process
        service=CaptureService(timeout=1,context=context)
        return service,parent,child,process,context

    def test_native_capture_timeout_terminates_only_the_capture_child(self):
        service,parent,child,process,context=self.service()
        with patch('autoplayer.capture_service.time.monotonic',side_effect=[0,2]):
            with self.assertRaises(CaptureTimeout):service.grab_png(123)
        self.assertIs(context.Process.call_args.kwargs['target'],capture_worker)
        process.terminate.assert_called_once();child.close.assert_called_once();parent.close.assert_called_once()
        self.assertIsNone(service.process)

    def test_cancellation_interrupts_capture_and_releases_its_lock(self):
        service,parent,_,process,_=self.service();cancel=threading.Event()
        parent.poll.side_effect=lambda _:cancel.set() or False
        with self.assertRaises(OperationCancelled):service.grab_png(123,cancel)
        process.terminate.assert_called_once()
        self.assertTrue(service.lock.acquire(blocking=False));service.lock.release()

    def test_preview_uses_validated_raw_pixels_without_changing_analysis_png_protocol(self):
        service,parent,_,_,_=self.service()
        value={'pixels':bytes([1,2,3]),'size':(1,1),'source_size':(1280,720)}
        parent.poll.return_value=True;parent.recv.return_value=('ok',value)
        self.assertEqual(service.grab_preview(123),value)
        parent.send.assert_called_once_with((123,'preview'));service.close()

    def test_invalid_preview_size_or_pixel_count_is_rejected(self):
        for value in ({'pixels':b'a','size':(1,1)},{'pixels':b'','size':(2000,2000)}):
            service,parent,_,_,_=self.service();parent.poll.return_value=True;parent.recv.return_value=('ok',value)
            with self.assertRaises(ValueError):service.grab_preview(123)
            service.close()

    def test_completed_capture_preserves_its_payload(self):
        service,parent,_,process,_=self.service();png=b'\x89PNG\r\n\x1a\ntransport-contract'
        parent.poll.return_value=True;parent.recv.return_value=('ok',png)
        self.assertEqual(service.grab_png(123),png)
        parent.send.assert_called_once_with(123);process.terminate.assert_not_called();service.close()


class UIResponsivenessTests(unittest.TestCase):
    def test_regular_decisions_without_event_and_retry_notifications_do_not_stop_play(self):
        from autoplayer.core import Settings
        app=Application.__new__(Application);app.recognition=__import__('autoplayer.telemetry',fromlist=['RecognitionState']).RecognitionState();app.stop=threading.Event();app.status=Mock()
        app.append_log=Mock();app.set_panel=Mock();app.cfg=Settings();app.traces=__import__('autoplayer.dashboard',fromlist=['LearningTraces']).LearningTraces()
        app.run_counts={'decisions':0,'sent':0,'changed':0,'retries':0}
        for event in ('absent',None,23):
            value={'scene':'unknown','subgoal':'現在の画面から進路を調べる','keys':['s']}
            if event!='absent':value['event']=event
            app.update_decision(value)
        app.update_decision({'status':'retry','reason':'再観測'})
        self.assertEqual(app.run_counts['decisions'],3);self.assertEqual(app.run_counts['retries'],1)
        self.assertFalse(app.stop.is_set())
        app.update_decision({'event':'human_focus','target_focused':True})
        self.assertIn('お手本記録中',app.status.set.call_args.args[0])

    def test_finished_worker_releases_preview_cancellation(self):
        app=Application.__new__(Application);app.queue=queue.Queue();app.queue.put(('done',None))
        app.control=Mock();app.closing_at=None;app.backend=SimpleNamespace(window_capture=SimpleNamespace(cancel=threading.Event()))
        app.start_button=Mock();app.pause_button=Mock();app.draw_frame=Mock();app.draw_learning_graph=Mock();app.set_panel=Mock()
        app.recognition_panel=Mock();app.recognition_floating=None;app.overlay=None
        app.run_started=None;app.run_finished=None;app.traces=__import__('autoplayer.dashboard',fromlist=['LearningTraces']).LearningTraces();app.run_counts={'sent':0,'changed':0,'retries':0};app.stop=threading.Event();app.busy=lambda:False
        app.poll_once();self.assertIsNone(app.backend.window_capture.cancel);self.assertIsNone(app.control)

    def test_poll_reschedules_itself_after_a_display_error(self):
        app=Application.__new__(Application);app.root=Mock();app.callback_error=Mock();app.arm_watchdog=Mock()
        app.poll_once=Mock(side_effect=ValueError('display error'))
        app.poll();app.callback_error.assert_called_once();app.root.after.assert_called_once_with(100,app.poll)

    def test_large_and_recursive_data_have_bounded_presentation(self):
        original={'text':'x'*100000,'rows':list(range(100000))};original['cycle']=original
        text=display_json(original,6000)
        self.assertLess(len(text),6100);self.assertEqual(len(original['text']),100000);self.assertEqual(len(original['rows']),100000)

    def test_window_close_does_not_wait_forever_for_a_worker(self):
        import autoplayer.ui as ui
        with tempfile.TemporaryDirectory() as tmp,patch.object(ui,'DATA',Path(tmp)):
            app=Application.__new__(Application);app.root=Mock();app.status=Mock();app.stop=threading.Event();app.preview_done=threading.Event()
            app.overlay=None;app.busy=lambda:True;app.closing_at=None;app.control=None;app.backend=None;app.watchdog_file=None;app.save_ui=Mock()
            with patch.object(ui.time,'monotonic',return_value=0):app.close()
            app.root.destroy.assert_not_called()
            with patch.object(ui.time,'monotonic',return_value=6):app.close()
            app.root.destroy.assert_called_once();self.assertTrue(app.stop.is_set())
            self.assertIn('5秒', (Path(tmp)/'ui-errors.log').read_text(encoding='utf-8'))

    def test_callback_error_is_logged_and_requests_stop(self):
        import autoplayer.ui as ui
        with tempfile.TemporaryDirectory() as tmp,patch.object(ui,'DATA',Path(tmp)):
            app=Application.__new__(Application);app.stop=threading.Event();app.status=Mock();app.control=SimpleNamespace(reason='')
            app.callback_error(ValueError,ValueError('broken display'),None)
            self.assertTrue(app.stop.is_set());self.assertIn('broken display',(Path(tmp)/'ui-errors.log').read_text(encoding='utf-8'))
            self.assertIn('表示処理のエラー: ValueError: broken display',app.control.reason)

    def test_importing_entrypoint_for_a_child_does_not_start_tk(self):
        with patch('autoplayer.ui.main') as start:
            importlib.import_module('autoplayer.__main__')
            start.assert_not_called()


if __name__=='__main__':unittest.main()
