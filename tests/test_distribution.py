"""Packaging contract: no external runtime, no histories, correct native architecture."""
import io
import struct
import unittest
import zipfile
from pathlib import Path
from tools.build_exe import payload

ROOT=Path(__file__).resolve().parents[1]

class DistributionTests(unittest.TestCase):
    def test_embedded_payload(self):
        data=payload()
        self.assertEqual(data,payload())
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertIsNone(z.testzip())
            self.assertIn('autoplayer/__main__.py',z.namelist())
            for name in z.namelist():
                self.assertTrue(name.startswith(('autoplayer/','knowledge/')))
                self.assertNotIn('__pycache__',name)
                self.assertFalse(name.endswith(('.dll','.exe','.onnx','.gguf','.pyc')))
            self.assertEqual(z.read('autoplayer/ui.py'),(ROOT/'autoplayer/ui.py').read_bytes())
    def test_windows_bootstrap_if_built(self):
        exe=ROOT/'dist/LocalGamePilot.exe'
        if not exe.exists(): self.skipTest('Native exe not built in this test job')
        data=exe.read_bytes()
        self.assertEqual(data[:2],b'MZ')
        offset=struct.unpack_from('<I',data,0x3c)[0]
        self.assertEqual(data[offset:offset+4],b'PE\0\0')
        self.assertEqual(struct.unpack_from('<H',data,offset+4)[0],0x8664)
        self.assertEqual(struct.unpack_from('<H',data,offset+24)[0],0x20b)
        self.assertEqual(struct.unpack_from('<H',data,offset+24+68)[0],2) # Windows GUI, not console.
        self.assertLess(len(data),512_000)

if __name__=='__main__': unittest.main()
