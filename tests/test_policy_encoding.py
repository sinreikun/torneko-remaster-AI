"""Windows CP932 default must not corrupt or reject distributed UTF-8 policy."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from autoplayer.nextgen.controller import load_policies

class PolicyEncodingTests(unittest.TestCase):
    def test_actual_policies_preserve_japanese_under_cp932_default(self):
        original=Path.read_text
        def windows_default(path,encoding=None,errors=None):return original(path,encoding=encoding or 'cp932',errors=errors)
        with patch.object(Path,'read_text',windows_default):
            spatial,execution=load_policies('トルネコ')
        self.assertEqual(spatial['field_reserved_meanings'],['マップ表示'])
        self.assertIn('上移動',execution['roles']['up']);self.assertIn('薬草',execution['items'][0]['names'])
    def test_generic_profile_does_not_load_game_specific_files(self):
        with patch.object(Path,'read_text',side_effect=AssertionError('unexpected file access')):
            self.assertEqual(load_policies('other game'),({},{}))
    def test_runtime_text_reads_require_explicit_encoding(self):
        import ast
        root=Path(__file__).parents[1]/'autoplayer'
        for path in root.rglob('*.py'):
            tree=ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='read_text':
                    self.assertTrue(node.args or any(k.arg=='encoding' for k in node.keywords),f'{path}:{node.lineno} depends on locale')
