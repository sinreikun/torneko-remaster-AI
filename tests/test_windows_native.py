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
            app.vars['timeout'].set('420')
            self.assertEqual(app.settings().timeout,420)
        finally:
            for token in root.tk.call('after','info'): root.after_cancel(token)
            root.destroy()

    def test_background_capture_and_input_do_not_steal_focus(self):
        import ctypes,io,threading,tkinter as tk,uuid
        from PIL import Image
        from autoplayer.windows import WindowsGame
        root=tk.Tk();title='Background fixture '+uuid.uuid4().hex
        root.title(title);root.geometry('500x300+80+80');root.configure(bg='#145c28')
        received=[];other=[]
        root.bind('<KeyPress>',lambda event:received.append(event.keycode))
        cover=tk.Toplevel(root);cover.title('Cover fixture');cover.geometry('500x300+80+80')
        cover.configure(bg='#b02020');cover.bind('<KeyPress>',lambda event:other.append(event.keycode))
        try:
            root.update();root.focus_force();root.update()
            backend=WindowsGame(title)
            cover.lift();cover.focus_force();root.update()
            foreground=backend.u.GetForegroundWindow()
            self.assertNotEqual(foreground,backend.hwnd)
            backend.activate()
            self.assertEqual(backend.u.GetForegroundWindow(),foreground)
            image=Image.open(io.BytesIO(backend.capture()))
            r,g,b=image.getpixel((image.width//2,image.height//2))
            self.assertGreater(g,r*2)
            backend.act(['d'],40,threading.Event());root.update()
            self.assertIn(ord('D'),received)
            self.assertEqual(other,[])
            self.assertEqual(backend.u.GetForegroundWindow(),foreground)
        finally:
            root.destroy()

    def test_numpad_input_is_independent_of_numlock(self):
        import ctypes,threading,tkinter as tk,uuid
        from autoplayer.windows import WindowsGame,INPUT,INPUTUNION,KEYBDINPUT
        root=tk.Tk();title='Key fixture '+uuid.uuid4().hex
        root.title(title);root.geometry('500x300');root.configure(bg='#2740ab')
        events=[];root.bind('<KeyPress>',lambda event:events.append(event.keycode))
        root.update();root.focus_force();root.update()
        backend=WindowsGame(title);backend.activate()
        backend.u.GetKeyState.argtypes=[ctypes.c_int];backend.u.GetKeyState.restype=ctypes.c_short
        original=backend.u.GetKeyState(0x90)&1
        def toggle():
            for flags in (0,2):
                event=INPUT(type=1,data=INPUTUNION(ki=KEYBDINPUT(0x90,0,flags,0,0)))
                self.assertEqual(backend.u.SendInput(1,ctypes.byref(event),ctypes.sizeof(INPUT)),1)
            root.update()
        try:
            for _ in range(2):
                events.clear();backend.act(['num2'],60,threading.Event());root.update()
                self.assertIn(0x62,events)
                toggle()
        finally:
            if backend.u.GetKeyState(0x90)&1!=original: toggle()
            backend.release_all();root.destroy()

if __name__=='__main__':unittest.main()
