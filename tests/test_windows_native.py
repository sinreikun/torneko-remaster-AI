"""Actually execute the native bootstrap on Windows, including non-ASCII temp paths."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

@unittest.skipUnless(os.name=='nt','Requires real Windows')
class NativeStartupTests(unittest.TestCase):
    def test_exe_with_unicode_and_quoted_temp_path(self):
        exe=Path(__file__).resolve().parents[1]/'dist/LocalGamePilot.exe'
        if not exe.exists(): self.skipTest('Native executable not built')
        with tempfile.TemporaryDirectory(prefix="日本語 O'Brien & ") as directory:
            env={**os.environ,'TEMP':directory,'TMP':directory}
            result=subprocess.run([str(exe),'--self-test'],env=env,timeout=90)
            logs='\n'.join(p.read_text(encoding='utf-8',errors='replace') for p in Path(directory).rglob('bootstrap.log'))
            self.assertEqual(result.returncode,0,logs[-8000:])

@unittest.skipUnless(os.name=='nt','Requires real Windows')
class WindowsAutomationTests(unittest.TestCase):
    def test_application_ui_initializes_without_manual_setup(self):
        import tkinter as tk
        from autoplayer.ui import Application
        root=tk.Tk();root.withdraw()
        try:
            app=Application(root);root.update_idletasks()
            settings=app.settings();settings.validate()
            self.assertFalse(app.dry.get())
            self.assertTrue(settings.knowledge)
            self.assertEqual(len(app.panels),13)
            self.assertFalse(app.separation.get())
            self.assertIsNone(app.backend)
            app.vars['timeout'].set('420')
            self.assertEqual(app.settings().timeout,420)
        finally:
            for token in root.tk.call('after','info'): root.after_cancel(token)
            if 'app' in locals():app.close()
            else:root.destroy()

    def test_real_application_capture_is_not_the_covering_ocr_window(self):
        import ctypes,io,tkinter as tk
        from ctypes import wintypes as W
        from PIL import ImageStat,ImageChops
        from autoplayer.ui import Application,DATA
        from autoplayer.ocr_ui import OCRTestWindow
        from autoplayer.window_capture import WindowCapture
        root=tk.Tk();app=Application(root);ocr=None
        try:
            root.geometry('1000x700+0+0');root.update()
            user=ctypes.WinDLL('user32');user.GetAncestor.argtypes=[W.HWND,W.UINT];user.GetAncestor.restype=W.HWND
            target=user.GetAncestor(root.winfo_id(),2)
            capture=WindowCapture();before=capture.grab(target)
            self.assertGreater(max(ImageStat.Stat(before).stddev),5,'Target capture must contain the real application UI')
            ocr=OCRTestWindow(root,None,DATA);ocr.window.geometry('1000x700+0+0');ocr.window.lift();ocr.window.focus_force();root.update()
            after=capture.grab(target)
            self.assertEqual(before.size,after.size)
            self.assertLess(max(ImageStat.Stat(ImageChops.difference(before,after)).mean),1.,'Covering OCR window must not appear in the main application capture')
        finally:
            if ocr:ocr.close()
            for token in root.tk.call('after','info'):root.after_cancel(token)
            app.close()

    def test_real_application_restores_saved_window_bounds(self):
        import json,time,tkinter as tk
        from unittest.mock import patch
        import autoplayer.ui as ui
        from autoplayer.placement import fit_bounds
        with tempfile.TemporaryDirectory() as temp,patch.object(ui,'DATA',Path(temp)):
            root=tk.Tk();app=ui.Application(root)
            try:
                root.update();root.geometry('1000x640+0+0');root.update()
                app.placement.track();app.save_ui()
                saved=json.loads((Path(temp)/'ui-settings.json').read_text(encoding='utf-8'))
                expected=fit_bounds(saved['window_bounds'],app.placement.monitors())
            finally:
                for token in root.tk.call('after','info'):root.after_cancel(token)
                app.close()
            root=tk.Tk();app=ui.Application(root)
            try:
                deadline=time.monotonic()+2
                while app.placement.restoring and time.monotonic()<deadline:
                    root.update();time.sleep(.01)
                root.update();app.placement.track()
                self.assertFalse(app.placement.restoring)
                actual=app.ui_state['window_bounds']
                self.assertEqual((actual['x'],actual['y']),(expected['x'],expected['y']))
                self.assertEqual((actual['width'],actual['height']),(expected['width'],expected['height']))
            finally:
                for token in root.tk.call('after','info'):root.after_cancel(token)
                app.close()

if __name__=='__main__':unittest.main()
