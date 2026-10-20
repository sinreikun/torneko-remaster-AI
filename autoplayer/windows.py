"""Capture and address the selected Windows window without stealing focus."""
import ctypes as C
from ctypes import wintypes as W
import io
import sys
import threading

class GUITHREADINFO(C.Structure):
    _fields_=[('cbSize',W.DWORD),('flags',W.DWORD),('hwndActive',W.HWND),
              ('hwndFocus',W.HWND),('hwndCapture',W.HWND),('hwndMenuOwner',W.HWND),
              ('hwndMoveSize',W.HWND),('hwndCaret',W.HWND),('rcCaret',W.RECT)]

class WindowsGame:
    def __init__(self, title, hwnd=None):
        if sys.platform != 'win32': raise RuntimeError('ゲーム画面の取得・操作には Windows が必要です。')
        from .capture_service import CaptureService
        self.window_capture=CaptureService()
        self.u=C.WinDLL('user32',use_last_error=True)
        self.u.GetForegroundWindow.restype=W.HWND
        self.u.PostMessageW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM]
        self.u.PostMessageW.restype=W.BOOL
        self.u.GetGUIThreadInfo.argtypes=[W.DWORD,C.POINTER(GUITHREADINFO)]
        self.u.GetGUIThreadInfo.restype=W.BOOL
        self.u.GetWindow.argtypes=[W.HWND,W.UINT]; self.u.GetWindow.restype=W.HWND
        self.u.IsChild.argtypes=[W.HWND,W.HWND]; self.u.IsChild.restype=W.BOOL
        self.u.IsWindow.argtypes=[W.HWND]; self.u.IsWindow.restype=W.BOOL
        self.u.IsIconic.argtypes=[W.HWND]; self.u.IsIconic.restype=W.BOOL
        self.u.GetWindowTextLengthW.argtypes=[W.HWND]
        self.u.GetWindowTextW.argtypes=[W.HWND,W.LPWSTR,C.c_int]
        self.u.GetClassNameW.argtypes=[W.HWND,W.LPWSTR,C.c_int]
        self.u.GetWindowRect.argtypes=[W.HWND,C.POINTER(W.RECT)]
        self.u.GetClientRect.argtypes=[W.HWND,C.POINTER(W.RECT)]
        self.u.ClientToScreen.argtypes=[W.HWND,C.POINTER(W.POINT)]
        self.u.GetAsyncKeyState.argtypes=[C.c_int]; self.u.GetAsyncKeyState.restype=C.c_short
        self.u.GetKeyState.argtypes=[C.c_int];self.u.GetKeyState.restype=C.c_short
        self.u.MapVirtualKeyW.argtypes=[W.UINT,W.UINT]; self.u.MapVirtualKeyW.restype=W.UINT
        self.u.ShowWindow.argtypes=[W.HWND,C.c_int]
        self.u.GetWindowThreadProcessId.argtypes=[W.HWND,C.POINTER(W.DWORD)]
        self.process_id=W.DWORD()

        try: self.u.SetProcessDPIAware()
        except AttributeError: pass
        self.hwnd=None; self.title=title.casefold(); self.held=set(); self.input_target=None
        self.capture_lock=threading.RLock();self.last_frame=None;self.last_capture_at=0
        if hwnd is None:self._find()
        else:
            if not self.u.IsWindow(hwnd):
                from .recovery import TargetUnavailable
                raise TargetUnavailable('選択したゲームウィンドウが閉じられました。')
            self.hwnd=hwnd
        self.u.GetWindowThreadProcessId(self.hwnd,C.byref(self.process_id))
    def _text(self,h):
        b=C.create_unicode_buffer(self.u.GetWindowTextLengthW(h)+1)
        self.u.GetWindowTextW(h,b,len(b)); return b.value
    def _find(self):
        matches=[]
        callback=C.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
        self.u.EnumWindows.argtypes=[callback,W.LPARAM]
        self.u.IsWindowVisible.argtypes=[W.HWND]
        @callback
        def visit(h,_):
            if self.u.IsWindowVisible(h) and self.title in self._text(h).casefold(): matches.append(h)
            return True
        self.u.EnumWindows(visit,0)
        if not matches:
            from pathlib import Path
            import json
            profile=Path(__file__).resolve().parent.parent/'knowledge/profile.json'
            from .controls import is_torneko
            if is_torneko(self.title) and profile.exists():
                spec=json.loads(profile.read_text(encoding='utf-8'))['window']
                kernel=C.WinDLL('kernel32',use_last_error=True)
                kernel.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];kernel.OpenProcess.restype=W.HANDLE
                kernel.QueryFullProcessImageNameW.argtypes=[W.HANDLE,W.DWORD,W.LPWSTR,C.POINTER(W.DWORD)]
                kernel.CloseHandle.argtypes=[W.HANDLE]
                @callback
                def identify(h,_):
                    name=C.create_unicode_buffer(256)
                    self.u.GetClassNameW(h,name,256)
                    if not self.u.IsWindowVisible(h) or name.value!=spec['class_name']: return True
                    pid=W.DWORD();self.u.GetWindowThreadProcessId(h,C.byref(pid))
                    handle=kernel.OpenProcess(0x1000,False,pid.value)
                    if handle:
                        try:
                            path=C.create_unicode_buffer(32768);size=W.DWORD(32768)
                            if kernel.QueryFullProcessImageNameW(handle,0,path,C.byref(size)) and Path(path.value).stem.casefold()==spec['process_name'].casefold(): matches.append(h)
                        finally: kernel.CloseHandle(handle)
                    return True
                self.u.EnumWindows(identify,0)
        if len(matches)!=1: raise RuntimeError(f'対象ウィンドウが {len(matches)} 個あります。固有のタイトルの一部を指定してください。')
        self.hwnd=matches[0]
    def ready(self):
        from .recovery import TargetUnavailable
        if not self.u.IsWindow(self.hwnd): raise TargetUnavailable('ゲームウィンドウが閉じられました。')
        current_pid=W.DWORD();self.u.GetWindowThreadProcessId(self.hwnd,C.byref(current_pid))
        if current_pid.value!=self.process_id.value:raise TargetUnavailable('接続先プロセスが変わりました。再観測が必要です。')
        if self.u.IsIconic(self.hwnd): raise RuntimeError('ゲームが最小化されています。')
        if self.emergency(): raise RuntimeError('緊急停止キーが押されました。')
    def capture(self):
        with self.capture_lock:
            image=self._capture()
            import time
            self.last_frame=image;self.last_capture_at=time.time()
            return image
    def _capture(self):
        self.ready()
        png=self.window_capture.grab_png(self.hwnd)
        self.ready()
        return png
    @staticmethod
    def difference(before_png,after_png):
        from PIL import Image, ImageChops, ImageStat
        before=Image.open(io.BytesIO(before_png)).convert('L').resize((160,120))
        after=Image.open(io.BytesIO(after_png)).convert('L').resize((160,120))
        return ImageStat.Stat(ImageChops.difference(before,after)).mean[0]
    def is_current(self,png): return self.difference(png,self.capture())<18
    def changed(self,before,after): return self.difference(before,after)>1.0
    def observe_turn(self,baseline,scene,wait_ms,stop):
        from .observation import TurnObserver
        if not hasattr(self,'observer'): self.observer=TurnObserver()
        return self.observer.observe(self.capture,baseline,scene,stop,wait_ms)
    def activate(self):
        # Kept for callers; SW_SHOWNOACTIVATE restores a minimized target without
        # bringing it above the operator UI or taking the user's keyboard focus.
        if self.u.IsIconic(self.hwnd): self.u.ShowWindow(self.hwnd,4)
        self.ready()
    def _input_window(self):
        info=GUITHREADINFO(); info.cbSize=C.sizeof(info)
        thread=self.u.GetWindowThreadProcessId(self.hwnd,None)
        if self.u.GetGUIThreadInfo(thread,C.byref(info)):
            if info.hwndFocus and self.u.IsChild(self.hwnd,info.hwndFocus):
                return info.hwndFocus
        # Tk wraps its input-bearing client in a top-level decoration HWND.
        name=C.create_unicode_buffer(256)
        self.u.GetClassNameW(self.hwnd,name,256)
        if name.value=='TkTopLevel':
            child=self.u.GetWindow(self.hwnd,5)  # GW_CHILD
            if child: return child
        return self.hwnd
    def _send(self,vk,up=False):
        scan=self.u.MapVirtualKeyW(vk,0)
        if not scan: raise RuntimeError('キーのスキャンコードを取得できません。')
        # Address only this HWND, including key-up after a focus change. Global
        # SendInput would otherwise send gameplay keys to the foreground app.
        flags=1 | (scan<<16) | ((1<<24) if vk in (0x25,0x26,0x27,0x28) else 0)
        if up: flags |= (1<<30) | (1<<31)
        target=self.input_target or self.hwnd
        if not self.u.PostMessageW(target,0x101 if up else 0x100,vk,flags):
            from .background import BackgroundUnavailable
            raise BackgroundUnavailable('対象への背景入力経路が利用できません。前面入力へ代替せず対象外として停止します。実行権限の相違も考えられます。')
        return {'os_submission':'accepted','target_hwnd':int(target),'message':'WM_KEYUP' if up else 'WM_KEYDOWN','vk':vk,'game_processing':'unverified'}
    def _vk(self,k):
        named={'up':0x26,'down':0x28,'left':0x25,'right':0x27,'enter':0x0d,'space':0x20,'escape':0x1b,'tab':0x09}
        if k in named: return named[k]
        if k.startswith('num'): return 0x60+int(k[3:])
        return ord(k.upper())
    def act(self,keys,ms,stop):
        from .controls import is_torneko,KEYS
        if is_torneko(getattr(self,'title','')) and any(key not in KEYS for key in keys):
            raise ValueError('現在のトルネコのキー配置にない入力は送信しません。Enter・Spaceは使用しません。')
        self.ready()
        background_before=self.is_background()
        self.input_target=self._input_window()
        receipts=[]
        try:
            for k in keys:
                self.ready()
                if stop.is_set(): break
                v=self._vk(k); self.held.add(v); receipts.append(self._send(v))
            import time
            end=time.monotonic()+ms/1000
            while time.monotonic()<end:
                self.ready()
                if stop.wait(min(.01,max(0,end-time.monotonic()))): break
        finally: receipts.extend(self.release_all())
        return {'os_submission':'accepted' if receipts else 'not_attempted','events':receipts,'game_processing':'unverified',
            'backend':'window_message_only','background_before':background_before,'background_after':self.is_background()}
    def capture_map_display(self,stop,baseline=False):
        """Capture while Num7 is held; release before OCR, planning or movement."""
        from .controls import is_torneko
        if not is_torneko(self.title):raise ValueError('地図表示観測は確認済みのトルネコ操作だけです。')
        with self.capture_lock:
            if stop.is_set():return {'status':'cancelled','learning':False}
            self.ready();self.input_target=self._input_window();events=[];first=None;image=None
            try:
                vk=self._vk('num7');self.held.add(vk);events.append(self._send(vk))
                if not stop.wait(.18):
                    first=self.capture()
                    if not baseline or not stop.wait(.12):image=self.capture() if baseline else first
            finally:events.extend(self.release_all())
            if image is None:return {'status':'cancelled','events':events,'learning':False}
            stop.wait(.18)
            restored=self.capture() if not stop.is_set() else None
            return {'status':'captured','image':image,'baseline_image':first,'restored_image':restored,
                'events':events,'learning':False,'kind':'held_map_display_not_keyless','game_processing':'unverified'}

    def release_all(self):
        if self.held and hasattr(self,'u') and hasattr(self.u,'IsWindow'):
            target=self.input_target or self.hwnd
            dead=not self.u.IsWindow(target)
            if not dead and hasattr(self,'process_id'):
                pid=W.DWORD();self.u.GetWindowThreadProcessId(target,C.byref(pid))
                dead=pid.value!=self.process_id.value
            if dead:
                self.held.clear();self.input_target=None
                return [{'os_submission':'not_attempted','reason':'target_closed_or_replaced_no_live_keys_to_release'}]
        errors=[];receipts=[]
        for v in list(self.held):
            try: receipts.append(self._send(v,True)); self.held.discard(v)
            except RuntimeError as error: errors.append(error)
        if not self.held: self.input_target=None
        if errors: raise errors[0]
        return receipts
    def probe_input(self):
        self.ready()
        # WM_NULL does not map to a game action. Accepted queue submission
        # says nothing about WM_KEYDOWN acceptance or game-side processing.
        if not self.u.PostMessageW(self.hwnd,0,0,0):raise RuntimeError('OSが対象へのメッセージ送信を拒否しました。実行権限と接続先を確認してください。')
        return {'os_submission':'accepted','message':'WM_NULL','target_hwnd':int(self.hwnd),
            'game_processing':'unverified','key_submission':'not_tested','no_keys_sent':True}
    def foreground(self): return self.u.GetForegroundWindow()
    def human_input_snapshot(self,allowed):
        """Read configured keys only while this exact target owns the foreground.
        No hooks, text input, other-window key history, or injected input.
        """
        self.ready()
        foreground=self.foreground()
        if foreground!=self.hwnd and not self.u.IsChild(self.hwnd,foreground):
            return {'target_focused':False,'keys':[]}
        if any(key.startswith('num') for key in allowed) and not self.u.GetKeyState(0x90)&1:
            return {'target_focused':True,'keys':[],'recording_blocked':'テンキーの記録にはNumLockをオンにしてください。オフ中の例は保存しません。'}
        keys=[key for key in allowed if self.u.GetAsyncKeyState(self._vk(key))&0x8000]
        if self.foreground()!=foreground:return {'target_focused':False,'keys':[]}
        return {'target_focused':True,'keys':keys}
    def is_background(self):
        foreground=self.u.GetForegroundWindow()
        if not foreground:return None
        pid=W.DWORD();self.u.GetWindowThreadProcessId(foreground,C.byref(pid))
        if not pid.value:return None
        return pid.value!=self.process_id.value
    def emergency(self):
        pressed=lambda vk:bool(self.u.GetAsyncKeyState(vk)&0x8000)
        return pressed(0x77) or (pressed(0x11) and pressed(0x10) and pressed(0x78))


def visible_windows():
    if sys.platform!='win32': return []
    u=C.WinDLL('user32',use_last_error=True)
    callback=C.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
    u.EnumWindows.argtypes=[callback,W.LPARAM]
    u.IsWindowVisible.argtypes=[W.HWND]
    u.GetWindowTextLengthW.argtypes=[W.HWND]
    u.GetWindowTextW.argtypes=[W.HWND,W.LPWSTR,C.c_int]
    titles=[]
    @callback
    def visit(h,_):
        if u.IsWindowVisible(h):
            text=C.create_unicode_buffer(u.GetWindowTextLengthW(h)+1)
            u.GetWindowTextW(h,text,len(text))
            if text.value and not text.value.startswith('ローカルLLM ゲーム攻略'): titles.append(text.value)
        return True
    u.EnumWindows(visit,0)
    return sorted(set(titles))


def process_alive(pid):
    if sys.platform!='win32' or not pid:return False
    kernel=C.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];kernel.OpenProcess.restype=W.HANDLE
    kernel.GetExitCodeProcess.argtypes=[W.HANDLE,C.POINTER(W.DWORD)]
    kernel.CloseHandle.argtypes=[W.HANDLE]
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:return C.get_last_error()!=87 # unknown/access denied is not proof of exit
    try:
        code=W.DWORD()
        return not kernel.GetExitCodeProcess(handle,C.byref(code)) or code.value==259
    finally:kernel.CloseHandle(handle)


def window_targets():
    """Visible top-level windows, kept distinct even with duplicate titles."""
    if sys.platform!='win32':return []
    import os
    u=C.WinDLL('user32',use_last_error=True);kernel=C.WinDLL('kernel32',use_last_error=True)
    callback=C.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
    u.EnumWindows.argtypes=[callback,W.LPARAM]
    u.IsWindowVisible.argtypes=[W.HWND];u.GetWindowTextLengthW.argtypes=[W.HWND]
    u.GetWindowTextW.argtypes=[W.HWND,W.LPWSTR,C.c_int]
    u.GetClassNameW.argtypes=[W.HWND,W.LPWSTR,C.c_int]
    u.GetWindowThreadProcessId.argtypes=[W.HWND,C.POINTER(W.DWORD)]
    kernel.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];kernel.OpenProcess.restype=W.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes=[W.HANDLE,W.DWORD,W.LPWSTR,C.POINTER(W.DWORD)]
    kernel.CloseHandle.argtypes=[W.HANDLE]
    targets=[]
    @callback
    def visit(hwnd,_):
        if not u.IsWindowVisible(hwnd):return True
        pid=W.DWORD();u.GetWindowThreadProcessId(hwnd,C.byref(pid))
        if pid.value==os.getpid():return True
        text=C.create_unicode_buffer(u.GetWindowTextLengthW(hwnd)+1);u.GetWindowTextW(hwnd,text,len(text))
        if not text.value:return True
        class_name=C.create_unicode_buffer(256);u.GetClassNameW(hwnd,class_name,256)
        executable='';handle=kernel.OpenProcess(0x1000,False,pid.value)
        if handle:
            try:
                path=C.create_unicode_buffer(32768);size=W.DWORD(32768)
                if kernel.QueryFullProcessImageNameW(handle,0,path,C.byref(size)):executable=path.value
            finally:kernel.CloseHandle(handle)
        targets.append({'hwnd':int(hwnd),'pid':pid.value,'title':text.value,'class_name':class_name.value,'executable':executable})
        return True
    u.EnumWindows(visit,0)
    return sorted(targets,key=lambda w:(w['title'].casefold(),w['pid'],w['hwnd']))


def emergency_stop_requested():
    if sys.platform!='win32':return False
    u=C.WinDLL('user32',use_last_error=True)
    u.GetAsyncKeyState.argtypes=[C.c_int];u.GetAsyncKeyState.restype=C.c_short
    pressed=lambda vk:bool(u.GetAsyncKeyState(vk)&0x8000)
    return pressed(0x77) or (pressed(0x11) and pressed(0x10) and pressed(0x78))
