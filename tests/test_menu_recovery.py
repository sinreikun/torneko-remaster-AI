"""OCR label and bounded recovery contracts from the reported menu failure."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from autoplayer.interfaces import torneko_menu_kind
from test_policy import state


def native(*labels):return {'status':'ok','lines':[{'text':s} for s in labels]}


class MenuObservationTests(unittest.TestCase):
    def setUp(self):
        self.inventory=native('道 具','3 / 20','詳 細 表 示 切 替','新 整 理')
        self.decision=SimpleNamespace(status='continue',risk='normal',keys=[])

    def test_spaced_main_menu_and_inventory_labels(self):
        self.assertEqual(torneko_menu_kind(self.inventory),'inventory')
        self.assertEqual(torneko_menu_kind(native('マ ッ プ','道 具','足 元','そ の 他')),'main_menu')
        self.assertIsNone(torneko_menu_kind(native('道具を使うと先へ進めます')))
        self.assertIsNone(torneko_menu_kind(native('道 具','3 / 20')))

    def test_policy_retains_menu_guard_even_with_visible_map(self):
        from autoplayer.policy import ExperiencePolicy
        policy=ExperiencePolicy.__new__(ExperiencePolicy)
        policy.reader=SimpleNamespace(reconcile=lambda _: {'metrics':state()['metrics']})
        with patch('autoplayer.policy.read_minimap',return_value=state()['minimap']):
            observed=policy.observe(b'coordinate contract',self.inventory)
        self.assertTrue(observed['menu_evidence']);self.assertFalse(observed['eligible'])

    def test_controller_explores_distinct_menu_inputs_instead_of_fixed_cancel(self):
        import tempfile,json
        from pathlib import Path
        from unittest.mock import Mock
        from autoplayer.core import Controller,Settings
        from autoplayer.memory import MemoryStore
        from test_agent import Script,Backend,change
        class ReceiptBackend(Backend):
            def act(self,keys,ms,stop):
                super().act(keys,ms,stop)
                return {'os_submission':'accepted','events':[{'message':'WM_KEYDOWN'}]}
        backend=ReceiptBackend()
        backend.observe_turn=Mock(return_value=SimpleNamespace(image=b'current',kind='no_change',changed=False,raw_change=0,masked_activity=0,duration_ms=1000))
        script=Script([change(scene='unknown',action={'keys':[],'hold_ms':1,'wait_ms':0})]*3)
        script.ocr=SimpleNamespace(read=Mock(return_value=self.inventory))
        menu_state=state();menu_state.update(eligible=False,menu_evidence=True,objective='interface')
        with tempfile.TemporaryDirectory() as folder:
            cfg=Settings(max_steps=3);memory=MemoryStore(Path(folder)/'memory.sqlite',cfg)
            try:
                agent=Controller(cfg,backend,script,lambda _:None,Path(folder)/'run.jsonl',memory=memory)
                agent.policy.observe=Mock(return_value=menu_state)
                agent.run()
                rows=[json.loads(l) for l in agent.journal.read_text().splitlines() if json.loads(l)['event']=='result']
            finally:memory.close()
        self.assertEqual(len(backend.actions),3)
        self.assertEqual(len({tuple(a[0]) for a in backend.actions}),3)
        self.assertTrue(all(a[0][0] in 'wasd' for a in backend.actions))
        self.assertEqual(rows[-1]['llm_proposal']['keys'],[])
        self.assertEqual(rows[-1]['decision_policy']['source'],'novelty_exploration')
        self.assertEqual(rows[-1]['policy_learning']['status'],'not_trained')
