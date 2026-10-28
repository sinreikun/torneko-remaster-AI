"""Input/frame attribution contracts. No fictional game or gameplay trial."""
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from PIL import Image,ImageDraw
from autoplayer.core import Settings,Controller
from autoplayer.exterior_screen import observe_change
from autoplayer.mapless_navigation import MaplessNavigation
from test_agent import Script,change

def png(image):
    stream=io.BytesIO();image.save(stream,format='PNG');return stream.getvalue()

def clear(color='#777755'):return png(Image.new('RGB',(640,360),color))
def framed():
    image=Image.new('RGB',(640,360),'#777755');ImageDraw.Draw(image).rectangle((50,150,580,320),fill='black',outline='white',width=3)
    return png(image)

class AttributionTests(unittest.TestCase):
    def make(self):
        cfg=Settings();n=MaplessNavigation(cfg)
        n.prepare({'exterior_action_policy':{'active':True,'mode':'exit_exploration'},'white_frame_evidence':{'visible':False}},{},{},0)
        return n
    def send(self,n,step,key,before,after,accepted=True):
        row={'step':step,'observed_keys':[key],'executed':True,'input_receipt':{'os_submission':'accepted' if accepted else 'rejected'}}
        return n.record(before,after,before,row)
    def test_large_change_and_white_frame_are_independent_measurements(self):
        e=observe_change(clear(),framed(),clear())
        self.assertTrue(e['large_change']);self.assertEqual(e['frame_transition'],'appeared')
    def test_failed_move_confirm_frame_does_not_become_successful_move(self):
        n=self.make();a=clear();b=framed()
        r=self.send(n,1,'w',a,a);self.assertFalse(r['last']['movement']['success'])
        r=self.send(n,2,'num1',a,b)
        self.assertEqual(n.pose,(0,0));self.assertEqual(len(n.nodes),1)
        self.assertTrue(r['last']['screen_change']['large_change'])
        self.assertEqual(r['last']['white_frame_observation']['transition'],'appeared')
        self.assertEqual(r['last']['movement']['status'],'not_applicable')
        self.assertEqual(r['last']['motion']['status'],'not_movement')
        self.assertEqual(r['last_movement']['step'],1);self.assertFalse(r['last_movement']['movement']['success'])
        self.assertEqual(r['last_interaction']['step'],2);self.assertIsNone(n.pending)
    def test_large_confirm_change_without_frame_never_moves(self):
        n=self.make();a=clear();b=clear('#224477')
        self.send(n,1,'w',a,a);r=self.send(n,2,'num1',a,b)
        self.assertTrue(r['last']['screen_change']['large_change']);self.assertEqual(n.pose,(0,0))
        self.assertEqual(r['last']['movement']['status'],'not_applicable')
        r=self.send(n,3,'w',b,b)
        self.assertEqual(n.pose,(0,0));self.assertFalse(r['last']['movement']['success'])
        self.assertFalse(r['last']['screen_change']['large_change'])
        self.assertEqual(r['last']['before_sha256'],hashlib.sha256(b).hexdigest())
    def test_frame_disappearance_on_confirm_is_not_movement(self):
        n=self.make();self.send(n,1,'w',clear(),clear());self.send(n,2,'num1',clear(),framed())
        r=self.send(n,3,'num1',framed(),clear())
        self.assertEqual(r['last']['white_frame_observation']['transition'],'disappeared')
        self.assertEqual(r['last']['movement']['status'],'not_applicable');self.assertEqual(n.pose,(0,0))
    def test_only_fresh_movement_pair_can_advance_after_confirm(self):
        n=self.make();a=clear();b=clear('#224477');c=clear('#775522')
        self.send(n,1,'w',a,a);self.send(n,2,'num1',a,b);r=self.send(n,3,'w',b,c)
        self.assertEqual(n.pose,(0,-1));self.assertEqual(len(n.nodes),2)
        self.assertTrue(r['last']['movement']['success']);self.assertEqual(r['last_movement']['step'],3)
        self.assertEqual(r['last_interaction']['step'],2)
    def test_wasd_with_frame_transition_is_suspended_even_when_change_large(self):
        n=self.make();r=self.send(n,1,'w',clear(),framed())
        self.assertTrue(r['last']['screen_change']['large_change'])
        self.assertEqual(r['last']['movement']['status'],'suspended_interface')
        self.assertIsNone(r['last']['movement']['success']);self.assertEqual(n.pose,(0,0));self.assertEqual(n.obstacles,set())
    def test_os_rejection_cannot_turn_large_change_into_movement(self):
        n=self.make();r=self.send(n,1,'w',clear(),clear('#224477'),accepted=False)
        self.assertTrue(r['last']['screen_change']['large_change'])
        self.assertEqual(r['last']['movement']['status'],'not_observed');self.assertEqual(n.pose,(0,0));self.assertTrue(n.uncertain)

class BaselineDispatchTests(unittest.TestCase):
    def run_contract(self,new_frame,initial_frame=None):
        a=initial_frame or clear()
        class Backend:
            def __init__(self):self.calls=0;self.actions=[]
            def capture(self):self.calls+=1;return a if self.calls==1 else new_frame
            def act(self,keys,ms,control):
                self.actions.append(keys);return {'os_submission':'accepted','events':[{'message':'WM_KEYDOWN'}]}
            def release_all(self):pass
        backend=Backend();script=Script([change(scene='gameplay',action={'keys':['w'],'hold_ms':80,'wait_ms':0})])
        script.ocr=SimpleNamespace(read=lambda png:{'status':'ok','lines':[],'frame_sha256':hashlib.sha256(png).hexdigest()})
        with tempfile.TemporaryDirectory() as t:
            agent=Controller(Settings(max_steps=1,fast_learning=False),backend,script,lambda _:None,Path(t)/'run')
            agent.run();rows=[json.loads(line) for line in agent.journal.read_text().splitlines()]
        return backend,rows
    def test_delayed_nonmovement_change_is_excluded_by_fresh_input_baseline(self):
        b=clear('#224477');backend,rows=self.run_contract(b)
        result=next(r for r in rows if r.get('event')=='result')
        self.assertEqual(backend.actions,[['w']])
        self.assertEqual(result['input_observation_baseline']['frame_sha256'],hashlib.sha256(b).hexdigest())
        self.assertFalse(result['movement_result']['success'])
        self.assertFalse(result['action_screen_observation']['screen_change']['large_change'])
    def test_frame_appearing_during_planning_cancels_wasd_before_dispatch(self):
        backend,rows=self.run_contract(framed())
        self.assertEqual(backend.actions,[])
        self.assertTrue(any(r.get('reason')=='white_frame_before_movement' for r in rows))
        self.assertFalse(any(r.get('event')=='sent' for r in rows))

    def test_direction_for_already_visible_interface_is_allowed_without_movement_credit(self):
        b=framed();backend,rows=self.run_contract(b,initial_frame=b)
        self.assertEqual(backend.actions,[['w']])
        result=next(r for r in rows if r.get('event')=='result')
        self.assertEqual(result['movement_result']['status'],'suspended_interface')
        self.assertIsNone(result['movement_result']['success'])
