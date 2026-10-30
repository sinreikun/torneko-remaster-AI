"""Copy the actual report ZIP as a Windows file attachment (CF_HDROP)."""
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
import struct
import sys

def copy_file(path,owner=None):
    path=Path(path).resolve()
    if not path.is_file(): raise FileNotFoundError(path)
    if sys.platform!='win32': raise RuntimeError('ファイルのクリップボード添付はWindowsで利用できます。')
    user=C.WinDLL('user32',use_last_error=True);kernel=C.WinDLL('kernel32',use_last_error=True)
    kernel.GlobalAlloc.argtypes=[W.UINT,C.c_size_t];kernel.GlobalAlloc.restype=W.HGLOBAL
    kernel.GlobalLock.argtypes=[W.HGLOBAL];kernel.GlobalLock.restype=C.c_void_p
    kernel.GlobalUnlock.argtypes=[W.HGLOBAL];kernel.GlobalFree.argtypes=[W.HGLOBAL]
    user.GetForegroundWindow.restype=W.HWND
    user.OpenClipboard.argtypes=[W.HWND];user.SetClipboardData.argtypes=[W.UINT,W.HANDLE];user.SetClipboardData.restype=W.HANDLE
    # DROPFILES: offset, POINT, fNC, fWide; UTF-16 path with double terminator.
    payload=struct.pack('<IiiII',20,0,0,0,1)+(str(path)+'\0\0').encode('utf-16-le')
    handle=kernel.GlobalAlloc(0x42,len(payload))
    if not handle: raise MemoryError('クリップボード領域を確保できません。')
    owned=True
    try:
        pointer=kernel.GlobalLock(handle)
        if not pointer: raise OSError('GlobalLock failed')
        try: C.memmove(pointer,payload,len(payload))
        finally: kernel.GlobalUnlock(handle)
        owner=owner or user.GetForegroundWindow()
        if not owner:raise OSError('コピー用のアプリウィンドウがありません。')
        if not user.OpenClipboard(owner): raise OSError('クリップボードが使用中です。再度コピーしてください。')
        try:
            if not user.EmptyClipboard() or not user.SetClipboardData(15,handle): raise OSError('ファイルをコピーできません。')
            owned=False
        finally: user.CloseClipboard()
    finally:
        if owned: kernel.GlobalFree(handle)
