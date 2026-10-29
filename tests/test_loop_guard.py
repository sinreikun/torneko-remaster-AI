import copy
import tempfile
import unittest
from pathlib import Path
from autoplayer.loop_guard import LoopGuard
from autoplayer.important_events import ImportantEvents

class LoopGuardTests(unittest.TestCase):
    def state(self,x=1,interface=None):
        return {'eligible':not interface,'interface_kind':interface,'metrics':{'floor':{'values':[1]},'hp':{'values':[20,20]}},'minimap':{'status':'observed','player_cell':[x,1],'accumulated_cells':[[1,1],[2,1]]}}
    def row(self,n):
        return {'step':n,'executed':True,'observed_keys':['d' if n%2 else 'a'],'input_receipt':{'os_submission':'accepted'}}
    def detect(self,g,menu=False):
        event=None
        for n in range(1,7):
            s=self.state(1+n%2,'inventory' if menu else None)
            event=g.record(self.row(n),s,s,{}) or event
        return event
    def test_spatial_cycle_forces_bounded_escape_without_floor_penalty(self):
        g=LoopGuard();event=self.detect(g)
        self.assertEqual(event['period'],2);self.assertEqual(event['kind'],'spatial')
        self.assertEqual(len(event['sequence']),6);self.assertFalse(event['learning'])
        s=self.state(3);e=g.record(self.row(7),s,s,{})
        self.assertEqual(e['event'],'loop_escape');self.assertIn('escape_observed',e['reason'])
    def test_combat_or_new_floor_information_is_progress(self):
        g=LoopGuard()
        for n in range(20):
            a=self.state();b=copy.deepcopy(a);b['metrics']['hp']['values']=[19,20]
            self.assertIsNone(g.record(self.row(n),a,b,{}))
        self.assertFalse(g.pending)
    def test_menu_exit_and_attempt_limit(self):
        g=LoopGuard();self.assertEqual(self.detect(g,True)['kind'],'menu')
        s=self.state();self.assertIn('menu_closed',g.record(self.row(7),s,s,{})['reason'])
        g=LoopGuard();self.detect(g)
        for n in range(7,15):
            s=self.state(1);e=g.record(self.row(n),s,s,{})
        self.assertIn('exhausted',e['reason']);self.assertIsNone(g.pending)
    def test_rejected_delivery_does_not_make_loop(self):
        g=LoopGuard()
        for n in range(20):
            row=self.row(n);row['input_receipt']['os_submission']='failed'
            self.assertIsNone(g.record(row,self.state(),self.state(),{}))
        self.assertFalse(g.history)
    def test_event_export_keeps_loop_sequence(self):
        g=LoopGuard();e=self.detect(g);row=self.row(6);row['loop_guard']=e
        with tempfile.TemporaryDirectory() as folder:
            ImportantEvents(folder).observe(row,None,None)
            import json
            saved=json.loads(next(Path(folder).rglob('event.json')).read_text())
            self.assertEqual(saved['action']['loop_guard']['sequence'],json.loads(json.dumps(e['sequence'])))
