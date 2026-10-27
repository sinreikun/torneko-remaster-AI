"""Recorded recovery-loop evidence and numerical/action-selection contracts."""
import copy,json,tempfile,unittest,threading
from pathlib import Path
from types import SimpleNamespace
from autoplayer.recovery_policy import RecoveryPolicy
from autoplayer.operational_control import OperationalControl
from autoplayer.action_effects import ActionEffects
from autoplayer.core import Settings,Decision
from autoplayer.memory import MemoryStore
from autoplayer.controls import REFERENCE
from autoplayer.supervision import StateLedger,Supervisor
from test_policy import state

class RecoveryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.supervisor=Supervisor(lambda *args:None,threading.Event());self.addCleanup(self.supervisor.close)
        self.cfg=Settings();self.memory=MemoryStore(Path(self.tmp.name)/'m.sqlite',self.cfg);self.addCleanup(self.memory.close)
        self.op=OperationalControl(self.cfg,ActionEffects(self.memory,self.cfg));self.base=Decision('menu','','','',.8,'continue',[],80,600)
    def fixture(self):return json.loads((Path(__file__).parent/'fixtures/recovery-loop-1793077088051765200.json').read_text())
    def ledger(self,s,n,step=1):
        l=StateLedger();l.observe(n,s,{'location':'dungeon'},{},{'revision':0,'text':''},0,step);return l
    def low(self):
        s=state();s['metrics'].update(hp={'values':[4,15]},fullness={'values':[99]});s['minimap'].update(player_cell=[10,10],known_cells=[[-1,0],[0,0],[0,1]])
        return s
    def test_recorded_inventory_without_hp_item_closes_both_menus_without_reentry(self):
        f=self.fixture()['frames'];s=f['2']['state'];n=f['2']['ocr'];n['_current_inventory_rows']=f['2']['inventory']['rows'];l=self.ledger(s,n)
        p=self.supervisor.bootstrap(l.current)
        self.assertEqual(self.op.interface(self.base,n,s,p,l,[]).keys,['num2'])
        self.assertEqual(self.op.recovery_policy.checks['hp']['status'],'no_known_recovery_item')
        for step in range(2,7):
            s=f['3']['state'];n=f['3']['ocr'];l=self.ledger(s,n,step)
            self.assertEqual(self.op.interface(self.base,n,s,self.supervisor.bootstrap(l.current),l,[]).keys,['num2'])
    def test_negative_check_survives_hp_and_floor_change_but_new_item_evidence_invalidates(self):
        f=self.fixture()['frames']['2'];s=copy.deepcopy(f['state']);n={'_current_inventory_rows':f['inventory']['rows']}
        r=RecoveryPolicy();r.record_unavailable(n,s,2);self.assertTrue(r.blocked(s))
        s['metrics']['hp']['values']=[3,15];s['metrics']['floor']['values']=[2];self.assertTrue(r.blocked(s))
        s['retained_state']['refresh_reasons']=['inventory_or_equipment_message'];s['retained_state']['revision']+=1;self.assertFalse(r.blocked(s))
        s=copy.deepcopy(f['state']);s['retained_state']['inventory_token']='new-inventory';self.assertFalse(r.blocked(s))
    def test_partial_list_or_cursor_failure_is_not_item_absence(self):
        s=self.low();s['retained_state']={'last_observed_inventory':{'occupancy':{'count':2}},'inventory_token':'same'}
        r=RecoveryPolicy();r.record_unavailable({'_current_inventory_rows':[{'display_name':'大きいパン'}]},s,1)
        self.assertEqual(r.checks['hp']['status'],'selection_or_coverage_unconfirmed')
        s['retained_state']['last_observed_inventory']['occupancy']['count']=1
        r.record_unavailable({'_current_inventory_rows':[{'display_name':'薬草'}]},s,2)
        self.assertEqual(r.checks['hp']['status'],'selection_or_coverage_unconfirmed')
    def test_food_check_does_not_inherit_missing_hp_item(self):
        f=self.fixture()['frames']['2'];s=copy.deepcopy(f['state']);r=RecoveryPolicy();r.record_unavailable({'_current_inventory_rows':f['inventory']['rows']},s,2)
        s['metrics']['fullness']['values']=[10];self.assertFalse(r.blocked(s))
    def test_wounded_move_guard_keeps_defense_and_full_hp_combat(self):
        s=self.low();s['minimap']['enemies']=[[3,0]];r=RecoveryPolicy()
        self.assertFalse(r.allows_move(s,['d'],REFERENCE['keys']));self.assertFalse(r.allows_move(s,['s','d'],REFERENCE['keys']))
        self.assertTrue(r.allows_move(s,['a'],REFERENCE['keys']));self.assertTrue(r.allows_move(s,['num1'],REFERENCE['keys']))
        s['minimap']['enemies']=[[1,0]];self.assertTrue(r.allows_move(s,['d'],REFERENCE['keys']))
        s['minimap']['enemies']=[[3,0]];s['metrics']['hp']['values']=[14,15];self.assertTrue(r.allows_move(s,['d'],REFERENCE['keys']))
    def test_retreat_requires_observed_destination_and_respects_failed_moves(self):
        s=self.low();s['minimap']['enemies']=[[1,0]];r=RecoveryPolicy()
        self.assertIsNone(r.retreat(s,REFERENCE['keys'],[]))
        s['map_route_hint']={'key':'a','keys':['a'],'target_kind':'stairs','target_cell':[9,10]}
        self.assertIsNone(r.retreat(s,REFERENCE['keys'],[]))
        s['minimap']['stairs']=[[-1,0]]
        self.assertEqual(r.retreat(s,REFERENCE['keys'],[]),'a')
        self.assertIsNone(r.retreat(s,REFERENCE['keys'],[['a']]))
    def test_natural_recovery_remains_available_after_negative_inventory_check(self):
        s=self.low();l=self.ledger(s,{'status':'ok','lines':[]})
        self.op.recovery_policy.record_unavailable({'_current_inventory_rows':[{'display_name':'大きいパン'}]},s,1)
        self.assertIsNotNone(self.op.natural_recovery(self.base,s,l.current,True,[]))
    def test_stationary_marks_require_accepted_turn_actions_and_clear_on_damage(self):
        r=RecoveryPolicy();s=self.low();s['minimap']['enemies']=[[4,0]]
        row={'executed':True,'observed_keys':['num6'],'input_receipt':{'os_submission':'accepted'},'policy_state':s,'policy_after':copy.deepcopy(s)}
        for _ in range(4):r.observe_result({**row,'executed':False},'num6')
        self.assertEqual(r.context(s)['stationary_enemy_cells'],[])
        for _ in range(3):r.observe_result(row,'num6')
        self.assertEqual(r.context(s)['stationary_enemy_cells'],[[14,10]])
        hurt=copy.deepcopy(s);hurt['metrics']['hp']['values']=[3,15]
        r.observe_result({**row,'policy_after':hurt},'num6');self.assertEqual(r.context(hurt)['stationary_enemy_cells'],[])
    def test_selector_does_not_reintroduce_approach_from_an_enemy_route(self):
        s=self.low();s['minimap']['enemies']=[[3,0]];s['map_route_hint']={'key':'d','keys':['d'],'target_kind':'enemy','distance':2}
        n={'status':'ok','lines':[]};l=self.ledger(s,n);plan=self.supervisor.bootstrap(l.current)
        # A completed negative check and no reachable retreat remove both loop and approach.
        self.op.recovery_policy.record_unavailable({'_current_inventory_rows':[{'display_name':'大きいパン'}]},s,1)
        s['minimap']['known_cells']=[[0,0]]
        result=self.op.select(self.base,s,n,l,plan,None,None,None,None,SimpleNamespace(context={'active':False}),[],True,{})
        self.assertEqual(result[0].keys,['num6']);self.assertEqual(result[4]['selected_source'],'defensive_turn')
    def test_facing_and_menu_inputs_do_not_accumulate_stationary_turns(self):
        r=RecoveryPolicy();s=self.low();s['minimap']['enemies']=[[4,0]]
        for keys in (['num5','d'],['num0']):
            row={'executed':True,'observed_keys':keys,'input_receipt':{'os_submission':'accepted'},'policy_state':s,'policy_after':copy.deepcopy(s)}
            for _ in range(4):r.observe_result(row,'num6')
        self.assertEqual(r.context(s)['stationary_enemy_cells'],[])
    def test_route_does_not_prioritize_passive_enemy_but_keeps_it_as_obstacle(self):
        from test_floor_navigation import observed
        from autoplayer.floor_navigation import FloorNavigation
        grid={(x,y) for x in range(1,8) for y in range(1,4)}
        s=observed((2,2),grid,enemies=[(6,2)]);nav=FloorNavigation()
        self.assertEqual(nav.hint(s)['target_kind'],'enemy')
        s['recovery_policy']={'stationary_enemy_cells':[[6,2]],'avoid_initiating_combat':False}
        hint=nav.hint(s)
        self.assertNotEqual((hint or {}).get('target_kind'),'enemy');self.assertIn((6,2),nav.enemies)
