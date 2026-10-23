import unittest
import sqlite3
from PIL import Image
from autoplayer.appearance import AppearanceBank
from autoplayer.core import Decision,Settings
from autoplayer.grounding import reconcile

class GroundingTests(unittest.TestCase):
    def decision(self,**kw):
        values=dict(scene='gameplay',observation='対象を確認',subgoal='接近する',reason='位置を確認',confidence=.8,status='continue',keys=['d'],hold_ms=100,wait_ms=200,intent={'kind':'approach','target_id':'object_1'})
        values.update(kw);return Decision(**values)
    def test_opposite_command(self):
        d,r=reconcile(self.decision(),{'objects':[{'id':'object_1','offset':[-2,0]}]},{'minimap':{'status':'observed','player_cell':[3,3],'accumulated_cells':[[2,3]]}},{},Settings())
        self.assertEqual(d.keys,['a']);self.assertEqual(r['status'],'corrected');self.assertFalse(d.forecast)
    def test_stale_target(self):
        d,r=reconcile(self.decision(),{'objects':[]},{},{},Settings());self.assertEqual(d.keys,[])
    def test_stairs_not_search(self):
        for text,expected in [('階段に向かう',[]),('階段を探す',['d'])]:
            d,r=reconcile(self.decision(intent={},subgoal=text),{'objects':[]},{},{},Settings());self.assertEqual(d.keys,expected)
    def test_adjacent_attack_not_assume_facing(self):
        d,r=reconcile(self.decision(keys=['num1'],intent={'kind':'attack','target_id':'object_1'}),{'objects':[{'id':'object_1','offset':[-1,0]}]},{'minimap':{'status':'observed'}},{},Settings());self.assertEqual(d.keys,['num1'])
    def test_appearance_persistence_and_variants(self):
        db=sqlite3.connect(':memory:');bank=AppearanceBank(db,'p')
        first=bank.observe(Image.new('RGB',(16,16),(180,40,30)))
        second=bank.observe(Image.new('RGB',(16,16),(175,45,30)),first['class_id'],'nw')
        self.assertEqual(second['class_id'],first['class_id']);self.assertEqual(second['sample_count'],2)
        restored=AppearanceBank(db,'p');other=restored.observe(Image.new('RGB',(16,16),(178,42,30)))
        self.assertEqual(other['similar_class_candidate'],first['class_id']);self.assertNotEqual(other['class_id'],first['class_id']);self.assertFalse(other['identity_verified'])
        self.assertFalse(AppearanceBank(db,'different').samples)
