"""Latest-observation and bounded-refresh contracts, not gameplay."""
import unittest
from autoplayer.state_snapshot import StateSnapshot
from autoplayer.world_model import Inventory

def native(*lines):return {'status':'ok','captured_at':1,'lines':[{'text':s} for s in lines]}
class SnapshotTests(unittest.TestCase):
    def test_values_are_overwritten_and_nonvisible_values_remain_past(self):
        m=StateSnapshot();m.observe({'hp':{'values':[10,15]},'strength':{'values':[8,8]}},{},1)
        m.observe({'hp':{'values':[12,15]}},{},2)
        self.assertEqual(m.context()['last_observed_metrics']['hp']['values'],[12,15])
        self.assertEqual(m.context()['last_observed_metrics']['strength']['last_observed_tick'],1)
        m.observe({'hp':{'values':[1,15],'retained':True}},{},3)
        self.assertEqual(m.context()['last_observed_metrics']['hp']['values'],[12,15])
    def test_inventory_missing_frame_does_not_erase_and_visible_read_replaces(self):
        m=StateSnapshot();m.observe({}, {'status':'observed','rows':[{'display_name':'薬草'}]},1)
        m.observe({}, {'status':'not_visible','rows':[]},2)
        self.assertEqual(m.context()['last_observed_inventory']['rows'][0]['display_name'],'薬草')
        m.observe({}, {'status':'observed','rows':[{'display_name':'パン'}]},3)
        self.assertEqual(m.context()['last_observed_inventory']['rows'],[{'display_name':'パン'}])
    def test_encounter_missing_abilities_requests_once_not_each_step(self):
        m=StateSnapshot()
        for i in range(5):m.observe({'hp':{'values':[10-i,15]}},{},i,enemies=True)
        self.assertEqual(m.revision,1);self.assertTrue(m.context()['before_combat_check_needed'])
        m.observe({}, {},5) # A hidden map/menu is not evidence that the enemy disappeared.
        m.observe({}, {},5,True);self.assertEqual(m.revision,1)
        m.observe({}, {'status':'observed','rows':[]},6,True)
        self.assertFalse(m.pending)
    def test_item_contact_requests_review_without_asserting_possession(self):
        m=StateSnapshot();before={'metrics':{'floor':{'values':[1]}},'minimap':{'player_cell':[2,2],'items':[[1,0]]}}
        after={'metrics':before['metrics'],'minimap':{'player_cell':[3,2]}}
        for _ in range(3):m.feedback(before,after,{}, {})
        self.assertEqual(m.revision,1);self.assertIsNone(m.inventory)
        m.feedback(before,after,{}, {},event_id=4)
        self.assertEqual(m.revision,2)
    def test_inventory_unreadable_frame_keeps_last_rows_but_explicit_empty_overwrites(self):
        inv=Inventory();inv.observe(native('道具','詳細表示切替','整理','3/20','▶薬草'))
        inv.observe(native('道具','詳細表示切替','整理','3/20'))
        self.assertEqual(inv.rows[0]['display_name'],'薬草')
        result=inv.observe(native('道具','詳細表示切替','整理','0/20'))
        self.assertEqual(result['status'],'observed');self.assertEqual(inv.rows,[])
