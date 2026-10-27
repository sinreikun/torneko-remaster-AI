"""A disposable child isolates PrintWindow, which has no native timeout API."""
import io
import multiprocessing
import threading
import time
from .background import BackgroundUnavailable
from .cancellation import OperationCancelled


class CaptureTimeout(RuntimeError):
    pass


def capture_worker(pipe):
    from .window_capture import WindowCapture
    try:
        capture=WindowCapture()
        while True:
            hwnd=pipe.recv()
            if hwnd is None:break
            try:
                image=capture.grab(hwnd);image.thumbnail((1280,960))
                output=io.BytesIO();image.save(output,format='PNG')
                pipe.send(('ok',output.getvalue()))
            except Exception as error:
                pipe.send(('unsupported' if isinstance(error,BackgroundUnavailable) else 'error',str(error)))
    except (EOFError,BrokenPipeError,OSError):pass
    finally:pipe.close()


class CaptureService:
    def __init__(self,timeout=4,context=None):
        self.timeout=timeout;self.context=context or multiprocessing.get_context('spawn')
        self.process=None;self.pipe=None;self.lock=threading.Lock();self.cancel=None

    def _start(self):
        if self.process is not None and self.process.is_alive():return
        self._discard()
        parent,child=self.context.Pipe()
        process=self.context.Process(target=capture_worker,args=(child,),daemon=True)
        try:process.start()
        except Exception:parent.close();child.close();raise
        child.close();self.process=process;self.pipe=parent

    def _discard(self):
        pipe,process=self.pipe,self.process;self.pipe=None;self.process=None
        if process is not None:
            if process.is_alive():process.terminate() # only our capture child, never the game
            process.join(timeout=.25)
            if not process.is_alive():process.close()
        if pipe is not None:pipe.close()

    def grab_png(self,hwnd,cancel=None):
        cancel=cancel or self.cancel
        deadline=time.monotonic()+self.timeout
        while not self.lock.acquire(timeout=.05):
            if cancel and cancel.is_set():raise OperationCancelled('画面取得を停止しました。')
            if time.monotonic()>=deadline:raise CaptureTimeout('画面取得の待機時間を超えました。')
        try:
            if cancel and cancel.is_set():raise OperationCancelled('画面取得を停止しました。')
            self._start();self.pipe.send(int(hwnd))
            while True:
                if cancel and cancel.is_set():
                    self._discard();raise OperationCancelled('画面取得を停止しました。')
                if time.monotonic()>=deadline:
                    self._discard();raise CaptureTimeout('画面取得が4秒以内に完了しませんでした。画面取得用の子プロセスだけを終了しました。')
                if self.pipe.poll(.05):
                    status,value=self.pipe.recv()
                    if status=='unsupported':raise BackgroundUnavailable(value)
                    if status!='ok':raise RuntimeError(value)
                    if not isinstance(value,bytes) or not value.startswith(b'\x89PNG\r\n\x1a\n'):raise ValueError('Invalid capture response')
                    return value
                if not self.process.is_alive():
                    self._discard();raise CaptureTimeout('画面取得用プロセスが終了しました。')
        except (EOFError,BrokenPipeError,OSError) as error:
            self._discard();raise CaptureTimeout('画面取得用プロセスとの接続を失いました。') from error
        finally:self.lock.release()

    def close(self):
        with self.lock:self._discard()
