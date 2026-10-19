"""Persist normal window bounds and maximized state across monitor changes."""
import os

def fit_bounds(saved,monitors):
    if not monitors:return None
    if not isinstance(saved,dict) or any(type(saved.get(k)) is not int for k in ('x','y','width','height')):return None
    x,y,w,h=(saved[k] for k in ('x','y','width','height'))
    if not 100<=w<=20000 or not 100<=h<=20000:return None
    def overlap(rect):
        a,b,c,d=rect
        return max(0,min(x+w,c)-max(x,a))*max(0,min(y+h,d)-max(y,b))
    monitor=max(monitors,key=overlap)
    if not overlap(monitor):monitor=monitors[0]
    left,top,right,bottom=monitor
    w=min(w,max(100,right-left-16));h=min(h,max(100,bottom-top-40))
    return {'x':max(left,min(x,right-w-16)),'y':max(top,min(y,bottom-h-40)),'width':w,'height':h}

class WindowPlacement:
    def __init__(self,root,settings):
        self.root=root;self.settings=settings;self.restoring=True
        root.bind('<Configure>',self.track,add='+')
        root.after_idle(self.restore)
    def monitors(self):
        if os.name!='nt':return [(0,0,self.root.winfo_screenwidth(),self.root.winfo_screenheight())]
        import ctypes as C
        from ctypes import wintypes as W
        u=C.WinDLL('user32')
        class INFO(C.Structure):_fields_=[('cbSize',W.DWORD),('rcMonitor',W.RECT),('rcWork',W.RECT),('dwFlags',W.DWORD)]
        callback=C.WINFUNCTYPE(W.BOOL,W.HANDLE,W.HDC,C.POINTER(W.RECT),W.LPARAM)
        u.GetMonitorInfoW.argtypes=[W.HANDLE,C.POINTER(INFO)]
        u.EnumDisplayMonitors.argtypes=[W.HDC,C.POINTER(W.RECT),callback,W.LPARAM]
        result=[]
        @callback
        def visit(handle,dc,rect,param):
            info=INFO();info.cbSize=C.sizeof(info)
            if u.GetMonitorInfoW(handle,C.byref(info)):
                r=info.rcWork;row=(r.left,r.top,r.right,r.bottom)
                if info.dwFlags&1:result.insert(0,row)
                else:result.append(row)
            return True
        u.EnumDisplayMonitors(None,None,visit,0);return result
    def native(self):
        import ctypes as C
        from ctypes import wintypes as W
        u=C.WinDLL('user32');u.GetAncestor.argtypes=[W.HWND,W.UINT];u.GetAncestor.restype=W.HWND
        return u,u.GetAncestor(self.root.winfo_id(),2)
    def restore(self):
        try:
            value=fit_bounds(self.settings.get('window_bounds'),self.monitors())
            if value and self.root.state()!='withdrawn':
                self.root.minsize(min(1000,value['width']),min(640,value['height']))
                self.root.geometry(f"{value['width']}x{value['height']}")
                self.root.update_idletasks()
                if os.name=='nt':
                    from ctypes import wintypes as W
                    u,hwnd=self.native();u.SetWindowPos.argtypes=[W.HWND,W.HWND,W.INT,W.INT,W.INT,W.INT,W.UINT]
                    u.SetWindowPos(hwnd,None,value['x'],value['y'],0,0,0x15)
                else:self.root.geometry(f"+{max(0,value['x'])}+{max(0,value['y'])}")
                if self.settings.get('window_state')=='zoomed' and os.name=='nt':self.root.state('zoomed')
        finally:self.restoring=False
    def track(self,event=None):
        if self.restoring or (event is not None and event.widget is not self.root):return
        state=self.root.state()
        if state not in ('normal','zoomed'):return
        self.settings['window_state']=state
        if state=='normal' and self.root.winfo_width()>100:
            x,y=self.root.winfo_x(),self.root.winfo_y()
            if os.name=='nt':
                import ctypes as C
                from ctypes import wintypes as W
                u,hwnd=self.native();u.GetWindowRect.argtypes=[W.HWND,C.POINTER(W.RECT)]
                rect=W.RECT()
                if u.GetWindowRect(hwnd,C.byref(rect)):x,y=rect.left,rect.top
            self.settings['window_bounds']={'x':x,'y':y,'width':self.root.winfo_width(),'height':self.root.winfo_height()}
