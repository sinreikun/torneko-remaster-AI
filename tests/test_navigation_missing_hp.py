"""Recorded HUD OCR loss must not invalidate current map coordinates."""
import copy,json,tempfile,unittest
from pathlib import Path
from autoplayer.core import Settings
from autoplayer.memory import MemoryStore
from autoplayer.policy import ExperiencePolicy
from autoplayer.floor_navigation import FloorNavigation
from autoplayer.recovery_policy import RecoveryPolicy
from autoplayer.controls import REFERENCE

class NavigationMissingHPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.memory=MemoryStore(Path(self.temp.name)/'memory.sqlite',Settings());self.addCleanup(self.memory.close)
        self.policy=ExperiencePolicy(self.memory,Settings())
        folder=Path(__file__).parent/'fixtures'
        self.data=json.loads((folder/'battle-hp-missing-1793138526674534800.json').read_text())
        self.png=(folder/'battle-map-1793138526674534800.png').read_bytes()
    def observe(self,reference=None):
        return self.policy.observe(self.png,map_display=True,reference=reference or self.data['reference'])
    def test_actual_bad_hp_ocr_keeps_current_dungeon_navigation_without_inventing_hp(self):
        self.assertIn('HP25725',[r['text'] for r in self.data['ocr']['lines']])
        s=self.observe();self.assertTrue(s['eligible']);self.assertNotIn('hp',s['metrics'])
        self.assertEqual(s['health_observation'],'unavailable_not_zero');self.assertTrue(FloorNavigation().observe(s))
    def test_hp_unreadability_does_not_change_map_location_or_scene_objective(self):
        first=self.observe();reference=copy.deepcopy(self.data['reference']);reference['metrics']['hp']={'values':[25,25]}
        readable=self.observe(reference);unreadable=self.observe()
        self.assertEqual(first['location'],readable['location']);self.assertEqual(unreadable['location'],readable['location'])
        self.assertEqual(first['objective'],readable['objective']);self.assertTrue(unreadable['eligible'])
    def test_menu_over_map_remains_ineligible_even_if_hp_is_missing(self):
        reference=copy.deepcopy(self.data['reference']);reference['menu_evidence']=True
        self.assertFalse(self.observe(reference)['eligible'])
    def test_unknown_hp_does_not_authorize_approaching_a_distant_enemy(self):
        s=self.observe();s['minimap']['enemies']=[[2,0]];policy=RecoveryPolicy()
        self.assertTrue(policy.context(s)['avoid_initiating_combat'])
        self.assertFalse(policy.allows_move(s,['d'],REFERENCE['keys']))
        self.assertTrue(policy.allows_move(s,['a'],REFERENCE['keys']))
        s['minimap']['enemies']=[];self.assertTrue(policy.allows_move(s,['d'],REFERENCE['keys']))
    def test_readable_map_with_missing_hp_offers_route_instead_of_unknown_key_sweep(self):
        from types import SimpleNamespace
        from autoplayer.operational_control import OperationalControl
        from autoplayer.action_effects import ActionEffects
        from autoplayer.core import Decision
        from autoplayer.supervision import StateLedger
        from test_supervision import proposal
        s=self.observe();nav=FloorNavigation();nav.observe(s);hint=nav.hint(s)
        self.assertIsNotNone(hint);s['map_route_hint']=hint
        ledger=StateLedger();ledger.observe(self.data['ocr'],s,{'location':'dungeon'},{},{'revision':0},0,92)
        cfg=Settings();actor=OperationalControl(cfg,ActionEffects(self.memory,cfg));plan=proposal();plan.update(id='hp-loss-case',skill='explore')
        base=Decision('gameplay','','','',.8,'continue',[],80,600)
        result=actor.select(base,s,self.data['ocr'],ledger,plan,None,None,None,None,SimpleNamespace(context={'active':False}),[],True,{})
        self.assertEqual(result[-1]['selected_source'],'floor_navigation')
        self.assertFalse(any(c['source']=='unknown_probe' for c in result[-1]['candidates']))
        reference=copy.deepcopy(self.data['reference']);reference['metrics']['hp']={'values':[25,25]}
        healthy=self.observe(reference);nav.observe(healthy);self.assertEqual(nav.hint(healthy)['target_cell'],hint['target_cell'])
