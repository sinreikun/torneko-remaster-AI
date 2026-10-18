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

if __name__=='__main__':unittest.main()
