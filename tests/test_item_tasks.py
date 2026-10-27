"""Marker/task/inventory observation contracts, without gameplay."""
import copy,json,unittest
from pathlib import Path
from autoplayer.item_tasks import ItemTasks
from autoplayer.floor_navigation import FloorNavigation
from autoplayer.world_model import Inventory
from autoplayer.telemetry import RecognitionState
from test_floor_navigation import observed
from test_policy import receipt_row

def native(*lines):return {'status':'ok','lines':[{'text':t} for t in lines]}
class ItemTaskTests(unittest.TestCase):
    def setUp(self):self.t=ItemTasks();self.t.observe({(2,1)},set(),set(),(1,1),'frame')
    def test_visited_cells_do_not_exclude_observed_item(self):
        nav=FloorNavigation();s=observed((1,1),{(1,1),(2,1),(3,1)},items=[(2,1)])
        nav.observe(s);nav.visited[(2,1)]=10
        self.assertEqual(nav.hint(s)['target_kind'],'items')
    def test_arrival_is_not_pickup_or_capacity_failure(self):
        self.t.arrive((2,1),1)
        task=self.t.tasks[(2,1)];self.assertEqual(task['status'],'arrival_unverified')
        self.assertIsNone(self.t.capacity);self.assertTrue(self.t.context()['needs_inventory_check'])
        self.t.observe(set(),set(),set(),(2,1),'after')
        self.assertNotEqual(task['status'],'pickup_confirmed')
    def test_capacity_message_holds_item_until_bag_space_confirmed(self):
        self.t.arrive((2,1),1);s=observed((2,1),{(1,1),(2,1)})
        self.t.feedback(receipt_row('d',step=1),s,s,native(),native('持ち物がいっぱいで拾えない'))
        self.assertEqual(self.t.tasks[(2,1)]['status'],'capacity_blocked')
        self.t.inventory('bag',{'count':20,'capacity':20});self.assertNotIn((2,1),self.t.goals((1,1)))
        self.t.inventory('changed',{'count':19,'capacity':20})
        self.assertIn((2,1),self.t.goals((1,1)))
    def test_new_pickup_message_with_position_confirms_but_disappearance_does_not(self):
        self.t.arrive((2,1),1);s=observed((2,1),{(1,1),(2,1)})
        self.t.feedback(receipt_row('d',step=1),s,s,native(),native('薬草を拾った。'))
        self.assertEqual(self.t.tasks[(2,1)]['status'],'pickup_confirmed')
        self.t.observe({(2,1)},set(),set(),(1,1),'new')
        self.assertEqual(self.t.tasks[(2,1)]['status'],'observed');self.assertEqual(self.t.tasks[(2,1)]['generation'],2)
        self.assertNotIn('evidence',self.t.tasks[(2,1)]);self.assertIn('previous_evidence',self.t.tasks[(2,1)])
    def test_unclassified_mark_is_retained_as_unknown_confirmation_task(self):
        nav=FloorNavigation();s=observed((1,1),{(1,1),(2,1),(3,1)})
        s['minimap']['uncertain_marks']=[[1,0]]
        hint=nav.hint(s);self.assertEqual(hint['target_kind'],'inspect_marker')
        task=nav.item_tasks.context()['tasks'][0];self.assertEqual(task['kind'],'unclassified_marker')
    def test_defer_has_reason_and_matching_revisit_trigger(self):
        self.t.decide([{'cell':[2,1],'decision':'defer','reason':'敵の対処を優先','revisit':'after_combat'}])
        self.assertFalse(self.t.goals((1,1)))
        self.t.inventory('bag',{'count':3,'capacity':20});self.assertEqual(self.t.tasks[(2,1)]['status'],'deferred')
        self.t.observe({(2,1)},set(),set(),(1,1),'a',True);self.t.observe({(2,1)},set(),set(),(1,1),'b',False)
        self.assertIn((2,1),self.t.goals((1,1)))
        self.assertEqual(self.t.tasks[(2,1)]['decision_reason'],'敵の対処を優先')
    def test_unchanged_inventory_does_not_rearm_endless_pickup_attempts(self):
        self.t.inventory('bag',{'count':3,'capacity':20});self.t.arrive((2,1),1);self.t.arrive((2,1),2)
        self.t.change(self.t.tasks[(2,1)],'awaiting_inventory_check','再確認')
        self.t.inventory('bag',{'count':3,'capacity':20})
        self.assertFalse(self.t.goals((1,1)))
    def test_inventory_does_not_treat_hud_as_possession_or_occupancy(self):
        i=Inventory();n=native('道具','詳細表示切替','整理','1階','HP20/20','3/20','▶薬草')
        result=i.observe(n);self.assertEqual(result['occupancy']['count'],3)
        self.assertEqual([r['display_name'] for r in result['rows']],['薬草'])
    def test_monitor_uses_live_icons_not_capacity_or_history_marks(self):
        s=observed((1,1),{(1,1),(2,1)},items=[(2,1)]);self.t.inventory('bag',{'count':20,'capacity':20})
        self.t.observe({(2,1)},set(),set(),(1,1),'frame2')
        s['floor_exploration']={'status':'observed_floor','item_tasks':self.t.context()}
        monitor=RecognitionState();monitor.update({'event':'perception','policy_state':s})
        self.assertEqual(monitor.grid()[(2,1)],'item');self.assertEqual(monitor.grid()[(1,1)],'player')
        self.assertIn('アイテム',monitor.item_detail((2,1)))
        self.assertNotIn('満杯',monitor.item_detail((2,1)))
        self.t.observe(set(),set(),set(),(1,1),'clear')
        self.assertNotIn((2,1),self.t.goals((1,1)))
        monitor.map['items']=[]
        self.assertNotEqual(monitor.grid()[(2,1)],'item')
        self.assertIn('アイコンなし',monitor.item_detail((2,1)))
        monitor.map['retained']=True;self.assertIn('保持',monitor.item_detail((2,1)))
    def test_actual_log_unknown_mark_becomes_a_confirmation_goal(self):
        data=json.loads((Path(__file__).parent/'fixtures/item-marker-1792973659845475500.json').read_text())
        nav=FloorNavigation();hint=nav.hint(data['state'])
        self.assertEqual(hint['target_kind'],'inspect_marker');self.assertEqual(hint['target_cell'],[18,28])

    def test_unknown_arrival_and_repeated_inventory_view_have_explicit_flags(self):
        t=ItemTasks();t.observe(set(),{(2,1)},set(),(1,1),'a');t.arrive((2,1),1)
        self.assertEqual(t.tasks[(2,1)]['status'],'classification_unresolved')
        self.assertFalse(t.context()['needs_inventory_check'])
        self.t.arrive((2,1),1);self.assertTrue(self.t.context()['needs_inventory_check'])
        self.t.inventory('bag',{'count':3,'capacity':20})
        self.assertFalse(self.t.context()['needs_inventory_check'])
        self.assertEqual(self.t.tasks[(2,1)]['status'],'awaiting_evidence')
    def test_unlocated_pickup_and_unchanged_display_are_not_falsely_assigned(self):
        row=receipt_row('d',step=1);before=observed((1,1),{(1,1),(2,1)})
        self.t.feedback(row,before,{'minimap':{'status':'unavailable'}},native(),native('薬草を拾った'))
        self.assertNotEqual(self.t.tasks[(2,1)]['status'],'pickup_confirmed')
        self.assertTrue(self.t.context()['unlocated_evidence'])
