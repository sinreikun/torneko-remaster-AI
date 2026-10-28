"""Windows no-activate, click-through overlay. No game injection or input hooks."""
import os

class OverlayAPI:
    def __init__(self):
        import ctypes as C
        from ctypes import wintypes as W
        self.C=C;self.W=W;self.u=C.WinDLL('user32',use_last_error=True)
        for name,args,ret in [
            ('IsWindow',[W.HWND],W.BOOL),('IsIconic',[W.HWND],W.BOOL),('IsChild',[W.HWND,W.HWND],W.BOOL),
            ('GetForegroundWindow',[],W.HWND),('GetAncestor',[W.HWND,W.UINT],W.HWND),
            ('GetClientRect',[W.HWND,C.POINTER(W.RECT)],W.BOOL),('ClientToScreen',[W.HWND,C.POINTER(W.POINT)],W.BOOL),
            ('GetWindowThreadProcessId',[W.HWND,C.POINTER(W.DWORD)],W.DWORD),
            ('SetWindowPos',[W.HWND,W.HWND,C.c_int,C.c_int,C.c_int,C.c_int,W.UINT],W.BOOL),
            ('ShowWindow',[W.HWND,C.c_int],W.BOOL)]:
            f=getattr(self.u,name);f.argtypes=args;f.restype=ret
        self.get=self.u.GetWindowLongPtrW;self.get.argtypes=[W.HWND,C.c_int];self.get.restype=C.c_ssize_t
        self.set=self.u.SetWindowLongPtrW;self.set.argtypes=[W.HWND,C.c_int,C.c_ssize_t];self.set.restype=C.c_ssize_t
    def configure(self,hwnd):
        hwnd=self.u.GetAncestor(hwnd,2)
        self.C.set_last_error(0)
        self.set(hwnd,-20,self.get(hwnd,-20)|0x08000000|0x00000020|0x00080000|0x00000080)
        if self.C.get_last_error():raise OSError('オーバーレイの入力透過設定に失敗しました。')
        if hasattr(self.u,'SetWindowDisplayAffinity'):
            self.u.SetWindowDisplayAffinity.argtypes=[self.W.HWND,self.W.DWORD];self.u.SetWindowDisplayAffinity.restype=self.W.BOOL
            self.u.SetWindowDisplayAffinity(hwnd,0x11)
        return hwnd
    def target_rect(self,hwnd,pid):
        if not hwnd or not self.u.IsWindow(hwnd) or self.u.IsIconic(hwnd):return None
        actual=self.W.DWORD();self.u.GetWindowThreadProcessId(hwnd,self.C.byref(actual))
        if actual.value!=pid:return None
        foreground=self.u.GetForegroundWindow()
        if foreground!=hwnd and not self.u.IsChild(hwnd,foreground):return None
        rect=self.W.RECT();point=self.W.POINT(0,0)
        if not self.u.GetClientRect(hwnd,self.C.byref(rect)) or not self.u.ClientToScreen(hwnd,self.C.byref(point)):return None
        return (point.x,point.y,rect.right,rect.bottom) if rect.right>0 and rect.bottom>0 else None
    def show(self,hwnd,rect):
        # HWND_TOPMOST with SWP_NOACTIVATE; never SetForegroundWindow.
        if not self.u.SetWindowPos(hwnd,self.W.HWND(-1),*rect,0x0010|0x0040):raise OSError('オーバーレイの配置に失敗しました。')
    def hide(self,hwnd):self.u.ShowWindow(hwnd,0)

class GameOverlay:
    def __init__(self,root,state,api=None):
        import tkinter as tk
        if os.name!='nt' and api is None:raise RuntimeError('ゲーム上のオーバーレイはWindows専用です。')
        self.state=state;self.api=api or OverlayAPI();self.target=None;self.pid=None;self.rect=None;self.last_revision=-1;self.layers={'map':True,'status':True,'objects':True,'forecast':True};self.last_second=-1
        self.window=tk.Toplevel(root);self.window.withdraw();self.window.overrideredirect(True)
        self.key='#010203';self.window.configure(bg=self.key);self.window.attributes('-transparentcolor',self.key)
        self.canvas=tk.Canvas(self.window,bg=self.key,highlightthickness=0);self.canvas.pack(fill='both',expand=True)
        self.window.update_idletasks()
        try:self.hwnd=self.api.configure(self.window.winfo_id())
        except Exception:self.window.destroy();raise
    def connect(self,backend):
        self.api.hide(self.hwnd);self.rect=None
        self.target=int(backend.hwnd) if backend else None
        self.pid=int(backend.process_id.value) if backend else None
    def set_layers(self,layers):
        self.layers.update(layers);self.last_revision=-1
    def refresh(self):
        from .telemetry import paint_grid
        rect=self.api.target_rect(self.target,self.pid) if self.target else None
        if rect is None:self.api.hide(self.hwnd);self.rect=None;return
        import time
        second=int(time.monotonic())
        if self.rect!=rect or self.last_revision!=self.state.revision or self.last_second!=second:
            self.rect=rect;self.last_revision=self.state.revision;self.last_second=second;self.canvas.delete('all')
            width,height=rect[2:];unit=min(5,max(2,(width-32)//54));x=max(8,width-54*unit-16);y=12
            show_map=self.layers['map'];show_text=self.layers['status'] or self.layers['forecast']
            base=y+32*unit if show_map else y
            if show_map or show_text:
                self.canvas.create_rectangle(x-5,y-5,width-7,min(height-5,base+140 if show_text else base+5),fill='#121c2b',outline='#568bb7')
            if show_map:paint_grid(self.canvas,self.state.grid(),x,y,unit)
            lines=self.state.summary().splitlines()
            text=[]
            if self.layers['status']:text+=lines[:2]
            if self.layers['forecast']:text+=[lines[2],('● LLM使用中' if self.state.thinking else '○ LLM待機')]
            if show_text:text+=lines[3:]
            if text:self.canvas.create_text(x,base+6,text='\n'.join(text),anchor='nw',width=54*unit,fill='#7dff91' if self.state.thinking else 'white',font=('',9))
            for obj in (self.state.objects[:24] if self.layers['objects'] else []):
                assoc=obj.get('screen_association',{})
                box=assoc.get('bbox_normalized')
                if box and len(box)==4:
                    a,b,c,d=[v*(width if i%2==0 else height) for i,v in enumerate(box)]
                else:
                    box=assoc.get('bbox');source=self.state.map.get('source_size',[])
                    if not box or len(source)!=2 or not source[0] or not source[1]:continue
                    if abs(source[0]/source[1]-width/height)>.03:continue
                    # Stored crop coordinates use source capture pixels.
                    a,b,c,d=[v*(width/source[0] if i%2==0 else height/source[1]) for i,v in enumerate(box)]
                self.canvas.create_rectangle(a,b,c,d,outline='#e6bf55',dash=(3,3),width=2)
                self.canvas.create_text(a,b-2,text=obj['id']+'（対応仮説）',anchor='sw',fill='#ffe6a2',font=('',9))
        self.api.show(self.hwnd,rect)
    def close(self):
        import tkinter as tk
        try:self.api.hide(self.hwnd)
        finally:
            try:self.window.destroy()
            except tk.TclError:pass
