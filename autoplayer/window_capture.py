"""Window-owned rendering only. Never substitute desktop BitBlt pixels."""
import ctypes as C
from ctypes import wintypes as W
import sys

class BITMAPINFOHEADER(C.Structure):
    _fields_=[('biSize',W.DWORD),('biWidth',W.LONG),('biHeight',W.LONG),
              ('biPlanes',W.WORD),('biBitCount',W.WORD),('biCompression',W.DWORD),
              ('biSizeImage',W.DWORD),('biXPelsPerMeter',W.LONG),('biYPelsPerMeter',W.LONG),
              ('biClrUsed',W.DWORD),('biClrImportant',W.DWORD)]
class BITMAPINFO(C.Structure):
    _fields_=[('bmiHeader',BITMAPINFOHEADER),('bmiColors',W.DWORD*3)]

class WindowCapture:
    def __init__(self):
        if sys.platform!='win32':raise RuntimeError('Windows is required')
        self.u=C.WinDLL('user32',use_last_error=True);self.g=C.WinDLL('gdi32',use_last_error=True)
        try:self.u.SetProcessDPIAware()
        except AttributeError:pass
        self.u.GetClientRect.argtypes=[W.HWND,C.POINTER(W.RECT)];self.u.GetClientRect.restype=W.BOOL
        self.u.GetDC.argtypes=[W.HWND];self.u.GetDC.restype=W.HDC
        self.u.ReleaseDC.argtypes=[W.HWND,W.HDC]
        self.u.PrintWindow.argtypes=[W.HWND,W.HDC,W.UINT];self.u.PrintWindow.restype=W.BOOL
        self.u.IsHungAppWindow.argtypes=[W.HWND];self.u.IsHungAppWindow.restype=W.BOOL
        self.g.CreateCompatibleDC.argtypes=[W.HDC];self.g.CreateCompatibleDC.restype=W.HDC
        self.g.CreateDIBSection.argtypes=[W.HDC,C.POINTER(BITMAPINFO),W.UINT,C.POINTER(C.c_void_p),W.HANDLE,W.DWORD]
        self.g.CreateDIBSection.restype=W.HBITMAP
        self.g.SelectObject.argtypes=[W.HDC,W.HANDLE];self.g.SelectObject.restype=W.HANDLE
        self.g.DeleteObject.argtypes=[W.HANDLE];self.g.DeleteDC.argtypes=[W.HDC]
        self.g.GdiFlush.argtypes=[];self.g.GdiFlush.restype=W.BOOL
    def grab(self,hwnd):
        from PIL import Image
        if self.u.IsHungAppWindow(hwnd):raise RuntimeError('ゲームが応答していないため画面を取得できません。')
        rect=W.RECT()
        if not self.u.GetClientRect(hwnd,C.byref(rect)):raise RuntimeError('ゲーム領域を取得できません。')
        width,height=rect.right,rect.bottom
        if width<32 or height<32 or width*height>32_000_000:raise RuntimeError('ゲーム画面の寸法が取得範囲外です。')
        source=self.u.GetDC(hwnd)
        if not source:raise RuntimeError('ゲームの描画コンテキストを取得できません。')
        dc=None;bitmap=None;previous=None
        try:
            dc=self.g.CreateCompatibleDC(source)
            if not dc:raise RuntimeError('キャプチャ用描画領域の確保に失敗しました。')
            info=BITMAPINFO();info.bmiHeader=BITMAPINFOHEADER(C.sizeof(BITMAPINFOHEADER),width,-height,1,32,0,width*height*4,0,0,0,0)
            pixels=C.c_void_p()
            bitmap=self.g.CreateDIBSection(source,C.byref(info),0,C.byref(pixels),None,0)
            if not bitmap or not pixels.value:raise RuntimeError('キャプチャ用画像の確保に失敗しました。')
            previous=self.g.SelectObject(dc,bitmap)
            if not previous or previous==C.c_void_p(-1).value:raise RuntimeError('キャプチャ画像を選択できません。')
            # PW_CLIENTONLY | PW_RENDERFULLCONTENT. PrintWindow requests the
            # TARGET's rendering, unlike ImageGrab(window=...) / GetDC BitBlt.
            if not self.u.PrintWindow(hwnd,dc,3):
                from .background import BackgroundUnavailable
                raise BackgroundUnavailable('ゲームの背面描画を取得できないため対象外です。デスクトップ画像への代用や前面化は行いません。')
            self.g.GdiFlush()
            return Image.frombytes('RGB',(width,height),C.string_at(pixels,width*height*4),'raw','BGRX')
        finally:
            if previous and previous!=C.c_void_p(-1).value:self.g.SelectObject(dc,previous)
            if bitmap:self.g.DeleteObject(bitmap)
            if dc:self.g.DeleteDC(dc)
            self.u.ReleaseDC(hwnd,source)
