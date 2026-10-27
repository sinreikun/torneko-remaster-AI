"""Real update packaging and fixed-launcher isolation, without simulating a game."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from tools import build_exe, publish_update

ROOT=Path(__file__).resolve().parents[1]

class UpdateDistributionTests(unittest.TestCase):
    def test_published_bundle_matches_manifest_and_current_sources(self):
        manifest=json.loads((ROOT/'release/latest.json').read_text())
        data=(ROOT/'release'/manifest['path']).read_bytes()
        self.assertEqual(manifest['protocol'],1)
        self.assertEqual(manifest['bytes'],len(data))
        self.assertEqual(manifest['sha256'],hashlib.sha256(data).hexdigest())
        self.assertEqual(manifest['path'],'apps/'+manifest['sha256']+'.zip')
        self.assertEqual(data,publish_update.bundle())
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(set(archive.namelist()),{'bootstrap.ps1','application.zip'})
            self.assertEqual(archive.read('application.zip'),build_exe.payload())
            self.assertEqual(archive.read('bootstrap.ps1').decode('utf-8-sig'),(ROOT/'launcher/bootstrap.ps1').read_text(encoding='utf-8-sig'))

    def test_launcher_embedded_bytes_do_not_depend_on_application(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);(base/'launcher').mkdir()
            for name in ('bootstrap.ps1','updater.ps1'):
                (base/'launcher'/name).write_bytes((ROOT/'launcher'/name).read_bytes())
            with patch.object(build_exe,'root',base),patch.object(build_exe,'payload',return_value=b'first app'):
                build_exe.generate();before=(base/'build/payload.h').read_bytes()
                (base/'launcher/bootstrap.ps1').write_text('new bootstrap version')
                with patch.object(build_exe,'payload',return_value=b'completely different application'):
                    build_exe.generate()
                self.assertEqual(before,(base/'build/payload.h').read_bytes())
                self.assertNotIn(b'application_zip',before)

if __name__=='__main__':unittest.main()
