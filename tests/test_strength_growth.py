"""Item-operation and current-status contracts, without game simulation."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from autoplayer.strength_growth import StrengthGrowth
from autoplayer.controls import REFERENCE

class StrengthGrowthTests(unittest.TestCase):
    def setUp(self):
        self.actor=SimpleNamespace(effects=SimpleNamespace(bindings=REFERENCE['keys']),cfg=SimpleNamespace(allowed_keys=list(REFERENCE['keys'])),_key=lambda role:{'menu':'num0','confirm':'num1','cancel':'num2'}.get(role),_cursor_toward=Mock(return_value='selected'),_decision=lambda base,keys,source,reason:SimpleNamespace(keys=keys,reason=reason))
        self.seed={'id':'seed','display_name':'ちからの種','quote':'ちからの種','selected':True}
        self.antidote={'id':'antidote','display_name':'毒消し草','quote':'毒消し草'}
        self.state={'eligible':True,'retained_state':{'last_observed_inventory':{'rows':[self.seed,self.antidote]},'last_observed_metrics':{'strength':{'values':[8,8]}}}}
        self.current={'step':1,'floor':[1],'control_revision':0,'operator_guidance':{'revision':0},'interface':'main_menu'}
        self.growth=StrengthGrowth()
    def choose(self,kind,values=None,rows=None):
        self.current.update(interface=kind,step=self.current['step']+1)
        s={**self.state,'metrics':{'strength':{'values':values}} if values else {}}
        return self.growth.choose(self.actor,None,{'_current_inventory_rows':rows or []},s,self.current)
    def accepted(self,keys):
        self.growth.feedback({'executed':True,'observed_keys':keys,'input_receipt':{'os_submission':'accepted'},'operation':{'selected_source':'strength_growth','selected_keys':keys}})
    def test_full_strength_selects_seed_then_exact_drink_and_requires_status_again(self):
        self.assertEqual(self.choose('main_menu',[8,8]).keys,['num1'])
        self.assertEqual(self.choose('inventory',rows=[self.seed,self.antidote]).keys,['num1'])
        self.assertEqual(self.growth.active['target']['display_name'],'ちからの種');self.accepted(['num1'])
        self.assertEqual(self.choose('item_actions').keys,['num1']);self.accepted(['num1'])
        self.assertTrue(self.growth.active['needs_status'])
        self.assertEqual(self.choose(None).keys,['num0'])
    def test_reduced_strength_uses_antidote_and_only_after_confirmed_full_switches_to_seed(self):
        self.choose('main_menu',[6,8]);self.choose('inventory',rows=[self.seed,self.antidote])
        self.assertEqual(self.growth.active['target']['display_name'],'毒消し草');self.accepted(['num1'])
        self.choose('item_actions');self.accepted(['num1'])
        self.assertTrue(self.growth.active['needs_status'])
        self.choose('main_menu',[8,8]);self.choose('inventory',rows=[self.seed])
        self.assertEqual(self.growth.active['target']['display_name'],'ちからの種')
    def test_missing_antidote_does_not_consume_seed(self):
        self.choose('main_menu',[6,8]);d=self.choose('inventory',rows=[self.seed])
        self.assertNotEqual(d.keys,['num1']);self.assertIsNone(self.growth.active['target'])
        self.assertIsNone(self.choose('inventory',rows=[self.seed]))
    def test_missing_or_retained_strength_cannot_authorize_seed(self):
        self.assertIsNone(self.choose('main_menu'))
        self.growth=StrengthGrowth();self.state['metrics']={'strength':{'values':[8,8],'retained':True}}
        self.assertIsNone(self.growth.choose(self.actor,None,{},self.state,self.current))
