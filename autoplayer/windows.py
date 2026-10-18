"""Windows foreground capture/input. Never inject into an unfocused window."""
import ctypes as C
from ctypes import wintypes as W
import io
import sys

class KEYBDINPUT(C.Structure):
    _fields_=[('wVk',W.WORD),('wScan',W.WORD),('dwFlags',W.DWORD),('time',W.DWORD),('dwExtraInfo',C.c_size_t)]
class MOUSEINPUT(C.Structure):
    _fields_=[('dx',W.LONG),('dy',W.LONG),('mouseData',W.DWORD),('dwFlags',W.DWORD),('time',W.DWORD),('dwExtraInfo',C.c_size_t)]
class HARDWAREINPUT(C.Structure):
    _fields_=[('uMsg',W.DWORD),('wParamL',W.WORD),('wParamH',W.WORD)]
class INPUTUNION(C.Union):
    _fields_=[('ki',KEYBDINPUT),('mi',MOUSEINPUT),('hi',HARDWAREINPUT)]
class INPUT(C.Structure):
    _fields_=[('type',W.DWORD),('data',INPUTUNION)]

class WindowsGame:
    def __init__(self, title):
        if sys.platform != 'win32': raise RuntimeError('ゲーム画面の取得・操作には Windows が必要です。')
        from PIL import ImageGrab
        self.grab=ImageGrab.grab
        self.u=C.WinDLL('user32',use_last_error=True)
        self.u.GetForegroundWindow.restype=W.HWND
        self.u.IsWindow.argtypes=[W.HWND]; self.u.IsWindow.restype=W.BOOL
        self.u.IsIconic.argtypes=[W.HWND]; self.u.IsIconic.restype=W.BOOL
        self.u.GetWindowTextLengthW.argtypes=[W.HWND]
        self.u.GetWindowTextW.argtypes=[W.HWND,W.LPWSTR,C.c_int]
        self.u.GetClassNameW.argtypes=[W.HWND,W.LPWSTR,C.c_int]
        self.u.GetClientRect.argtypes=[W.HWND,C.POINTER(W.RECT)]
        self.u.ClientToScreen.argtypes=[W.HWND,C.POINTER(W.POINT)]
        self.u.GetAsyncKeyState.argtypes=[C.c_int]; self.u.GetAsyncKeyState.restype=C.c_short
        self.u.SendInput.argtypes=[W.UINT,C.POINTER(INPUT),C.c_int]; self.u.SendInput.restype=W.UINT
        self.u.MapVirtualKeyW.argtypes=[W.UINT,W.UINT]; self.u.MapVirtualKeyW.restype=W.UINT
        self.u.SetForegroundWindow.argtypes=[W.HWND]; self.u.SetForegroundWindow.restype=W.BOOL
        self.u.ShowWindow.argtypes=[W.HWND,C.c_int]
        self.u.GetWindowThreadProcessId.argtypes=[W.HWND,C.POINTER(W.DWORD)]
        self.process_id=W.DWORD()

        try: self.u.SetProcessDPIAware()
        except AttributeError: pass
        self.hwnd=None; self.title=title.casefold(); self.held=set()
        self._find()
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
            if profile.exists():
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
        if not self.u.IsWindow(self.hwnd): raise RuntimeError('ゲームウィンドウが閉じられました。')
        if self.u.IsIconic(self.hwnd): raise RuntimeError('ゲームが最小化されています。')
        if self.u.GetForegroundWindow()!=self.hwnd: raise RuntimeError('ゲームが前面でないため停止しました。ゲームを前面にして再開してください。')
        if self.u.GetAsyncKeyState(0x77)&0x8000: raise RuntimeError('F8 による緊急停止')
        if any(self.u.GetAsyncKeyState(v)&0x8000 for v in (0x10,0x11,0x12,0x5b,0x5c)):
            raise RuntimeError('修飾キーが押されているため停止しました。')
    def capture(self):
        self.ready()
        r=W.RECT(); p=W.POINT(0,0)
        if not self.u.GetClientRect(self.hwnd,C.byref(r)) or not self.u.ClientToScreen(self.hwnd,C.byref(p)):
            raise RuntimeError('クライアント領域の取得に失敗しました。')
        if r.right<32 or r.bottom<32: raise RuntimeError('ゲーム領域が小さすぎます。')
        img=self.grab(bbox=(p.x,p.y,p.x+r.right,p.y+r.bottom),all_screens=True)
        self.ready()
        img.thumbnail((1280,960))
        out=io.BytesIO(); img.save(out,format='PNG'); return out.getvalue()
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
        self.u.ShowWindow(self.hwnd,9)
        self.u.SetForegroundWindow(self.hwnd)
        self.ready()
    def _send(self,vk,up=False):
        scan=self.u.MapVirtualKeyW(vk,0)
        if not scan: raise RuntimeError('キーのスキャンコードを取得できません。')
        flags=0x0008 | (0x0002 if up else 0) | (0x0001 if vk in (0x25,0x26,0x27,0x28) else 0)
        event=INPUT(type=1,data=INPUTUNION(ki=KEYBDINPUT(0,scan,flags,0,0)))
        if self.u.SendInput(1,C.byref(event),C.sizeof(INPUT))!=1:
            raise RuntimeError('Windows がキー入力を受け付けませんでした。')
    def _vk(self,k):
        named={'up':0x26,'down':0x28,'left':0x25,'right':0x27,'enter':0x0d,'space':0x20,'escape':0x1b,'tab':0x09}
        if k in named: return named[k]
        if k.startswith('num'): return 0x60+int(k[3:])
        return ord(k.upper())
    def act(self,keys,ms,stop):
        self.ready()
        try:
            for k in keys:
                self.ready()
                if stop.is_set(): return
                v=self._vk(k); self.held.add(v); self._send(v)
            import time
            end=time.monotonic()+ms/1000
            while time.monotonic()<end:
                self.ready()
                if stop.wait(min(.01,max(0,end-time.monotonic()))): break
        finally: self.release_all()
    def release_all(self):
        errors=[]
        for v in list(self.held):
            try: self._send(v,True); self.held.discard(v)
            except RuntimeError as error: errors.append(error)
        if errors: raise errors[0]
    def emergency(self): return bool(self.u.GetAsyncKeyState(0x77)&0x8000)


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
