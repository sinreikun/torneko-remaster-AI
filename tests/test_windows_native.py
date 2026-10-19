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

if __name__=='__main__':unittest.main()
