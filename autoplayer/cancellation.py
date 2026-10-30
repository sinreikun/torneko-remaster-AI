"""Interruptible waits; pending operations never authorize a later game input."""
import queue
import socket
import threading
import time


class OperationCancelled(RuntimeError):
    pass


def call_interruptible(fn,cancel=None,on_cancel=None):
    if cancel is None:return fn()
    if cancel.is_set():raise OperationCancelled('停止または一時停止を受け付けました。')
    completed=queue.Queue(maxsize=1)
    def work():
        try:completed.put((True,fn()))
        except Exception as error:completed.put((False,error))
    threading.Thread(target=work,daemon=True).start()
    while True:
        if cancel.is_set():
            if on_cancel:
                try:on_cancel()
                except OSError:pass
            raise OperationCancelled('停止または一時停止を受け付け、処理結果を破棄しました。')
        try:ok,value=completed.get(timeout=.05)
        except queue.Empty:continue
        if cancel.is_set():raise OperationCancelled('停止または一時停止後の処理結果を破棄しました。')
        if ok:return value
        raise value


def request_bytes(request,timeout,cancel=None):
    """The connection can be shut down while waiting for HTTP response headers."""
    import http.client
    import io
    import urllib.error
    from urllib.parse import urlsplit
    url=urlsplit(request.full_url)
    if url.scheme not in ('http','https'):raise ValueError('Invalid HTTP scheme')
    connection=(http.client.HTTPSConnection if url.scheme=='https' else http.client.HTTPConnection)(url.hostname,url.port,timeout=timeout)
    def abort():
        stream=connection.sock
        if stream:
            try:stream.shutdown(socket.SHUT_RDWR)
            except OSError:pass
        connection.close()
    def fetch():
        try:
            path=(url.path or '/')+('?' +url.query if url.query else '')
            connection.request(request.get_method(),path,body=request.data,headers=dict(request.header_items()))
            response=connection.getresponse();data=response.read(2_000_001)
            if len(data)>2_000_000:raise ValueError('LLM response too large')
            if response.status>=300:raise urllib.error.HTTPError(request.full_url,response.status,response.reason,response.headers,io.BytesIO(data))
            return data
        finally:connection.close()
    return call_interruptible(fetch,cancel,abort)


def run_process(args,timeout,cancel=None,creationflags=0):
    """Stop only this tool's OCR process, never the selected game."""
    import subprocess
    with subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=creationflags) as process:
        deadline=time.monotonic()+timeout
        while True:
            if cancel and cancel.is_set():
                process.kill();process.communicate()
                raise OperationCancelled('OCRを停止しました。')
            remaining=deadline-time.monotonic()
            if remaining<=0:
                process.kill();process.communicate()
                raise subprocess.TimeoutExpired(args,timeout)
            try:out,err=process.communicate(timeout=min(.1,remaining))
            except subprocess.TimeoutExpired:continue
            return subprocess.CompletedProcess(args,process.returncode,out,err)
