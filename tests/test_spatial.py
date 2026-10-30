"""Spatial data/ownership contracts, not a demo game or proof of gameplay."""
import copy,unittest,tempfile
from pathlib import Path
from autoplayer.nextgen.spatial import SpatialModel,SpatialPlanner,SpatialLane,current_target,trace
from autoplayer.nextgen.capabilities import CapabilityManager
from autoplayer.nextgen.progress import ProgressManager
from autoplayer.nextgen.store import ExperienceStore
from test_nextgen import measured,action_id
from PIL import Image
import io

def state(frame='a',pose=(1,1),known=((1,1),(2,1),(3,1)),**extra):
    s=measured(frame);s.update(map_domain='floor-domain',floor_confirmation={'confirmed':1,'status':'confirmed'},interface_hypothesis=False)
    s['minimap']={'status':'observed','player_cell':list(pose),'accumulated_cells':[list(p) for p in known],'grid_size':[8,8],'items':[[3-pose[0],1-pose[1]]],'enemies':[],'stairs':[]};s.update(extra);return s

def texture():
    im=Image.new('RGB',(96,72));im.putdata([(i%255,(i*3)%255,(i*7)%255) for i in range(96*72)]);o=io.BytesIO();im.save(o,format='PNG');return o.getvalue()

class SpatialContracts(unittest.TestCase):
    def test_measured_coordinates_icons_bounds_and_revisits(self):
        m=SpatialModel();v=m.update(state(),0);ts=SpatialPlanner.candidates(v)
        item=next(t for t in ts if t['kind']=='item');self.assertEqual(item['cell'],[3,1]);self.assertEqual(item['distance'],2)
        self.assertTrue(all(0<=t['cell'][0]<8 and 0<=t['cell'][1]<8 for t in ts));self.assertEqual(item['source'],'OBSERVED')
        later=m.update(state('b',pose=(3,1)),1);back=next(t for t in SpatialPlanner.candidates(later) if t['kind']=='frontier' and t['cell']==[0,1])
        self.assertEqual(back['path'][:3],[[3,1],[2,1],[1,1]])
    def test_target_revision_never_matches_another_frame(self):
        lane=SpatialLane();s=state();lane.begin(s,0,1);a=lane.merge('a',0,1)['view'];s['spatial']=a;t=a['targets'][0]
        self.assertTrue(current_target(s,t));s['frame_sha256']='new';self.assertFalse(current_target(s,t))
        lane.begin(state('b'),1,1)
        with self.assertRaises(ValueError):lane.merge('b',1,2)
        self.assertEqual(lane.revision,1)
        lane.begin(state('c'),2,2);self.assertEqual(lane.merge('c',2,2)['view']['stamp']['revision'],2);lane.close()
    def test_retained_map_keeps_evidence_and_menu_does_not_destroy_map(self):
        m=SpatialModel();a=m.update(state(),0);s=state('b');s['minimap']['status']='retained';b=m.update(s,1)
        self.assertEqual(b['pose_source'],'RETAINED');self.assertEqual(b['region_data']['cells']['1,1']['evidence']['frame'],'a')
        c=state('c',interface_hypothesis=True);c['minimap']={'status':'unavailable'};v=m.update(c,2)
        self.assertEqual(v['status'],'suspended_interface');self.assertEqual(v['region_data']['cells'],b['region_data']['cells'])
    def test_observed_failure_is_hypothesis_not_backend_error_and_confirm_is_not_movement(self):
        m=SpatialModel();a=state();v=m.update(a,0);a['spatial']=v
        tr={'before':a,'intent':{'role':'move','delta':[1,0]},'accepted':True,'hold_ms':140}
        for n in (1,2):v=m.update(state(str(n)),n,tr)
        self.assertIn('2,1',v['region_data']['obstacles']);self.assertEqual(v['region_data']['obstacles']['2,1']['source'],'HYPOTHESIS')
        self.assertEqual(v['movement']['status'],'refuted_displacement')
        tr['intent']={'role':'interact'};m.update(state('dialog',interface_hypothesis=True),3,tr)
        self.assertEqual(m.regions[m.active]['pose'],[1,1])
    def test_capability_maps_waypoint_through_user_bindings_without_fixed_keys(self):
        lane=SpatialLane();s=state();lane.begin(s,0);s['spatial']=lane.merge('a',0)['view'];target=next(t for t in s['spatial']['targets'] if t['kind']=='item')
        with tempfile.TemporaryDirectory() as d:
            store=ExperienceStore(Path(d)/'s.sqlite','p','s');cap=CapabilityManager(store)
            cs=[{'id':action_id([k],80),'keys':[k],'hold_ms':80,'declared_meanings':[meaning]} for k,meaning in [('custom','right'),('notmove','menu')]]
            r=cap.realize_spatial(s,target,cs);self.assertEqual(r['candidate_ids'],[cs[0]['id']]);store.close()
        lane.close()
    def test_progress_selects_near_frontier_and_keeps_destination_through_transit(self):
        lane=SpatialLane();s=state();lane.begin(s,0);v=lane.merge('a',0)['view']
        with tempfile.TemporaryDirectory() as d:
            store=ExperienceStore(Path(d)/'s.sqlite','p','s');p=ProgressManager(store)
            first=p.select_spatial(v,{'capability':'move','conditions':[]},{'priorities':{'item':2,'frontier':1}})
            self.assertEqual(first['kind'],'item')
            lane.begin(state('b',pose=(2,1)),1);v=lane.merge('b',1)['view'];second=p.select_spatial(v,{'capability':'move','conditions':[]},{'priorities':{'item':2,'frontier':1}})
            self.assertEqual(first['id'],second['id']);self.assertNotEqual(first['stamp'],second['stamp']);store.close()
        lane.close()
    def test_hypothesis_movement_region_boundary_and_relocalization(self):
        m=SpatialModel();s=state();s['minimap']={'status':'unavailable'};s['interface_hypothesis']=False
        png=texture();v=m.update(s,0,png=png);region=v['stamp']['region'];s['spatial']=v
        tr={'before':s,'accepted':True,'intent':{'role':'move','delta':[1,0]},'motion':{'status':'translated'},'boundary':{}}
        v=m.update({**s,'frame_sha256':'b'},1,tr,png);self.assertEqual(v['pose'],[1,0]);self.assertEqual(v['pose_source'],'HYPOTHESIS')
        # A blackout must not draw a connecting floor edge across scenes.
        tr['boundary']={'blackout_seen':True};v=m.update({**s,'frame_sha256':'c'},2,tr,None)
        self.assertNotEqual(v['stamp']['region'],region)
        saved=m.dump();reloaded=SpatialModel(saved)
        a=reloaded.update(s,0,png=png);self.assertEqual(a['status'],'relocalization_ambiguous')
        b=reloaded.update({**s,'frame_sha256':'other'},1,png=png)
        # Identical appearances at multiple poses must remain ambiguous.
        self.assertNotEqual(b['movement']['status'],'relocalized')

    def test_unique_anchor_restores_only_after_two_observations(self):
        m=SpatialModel();s=state();s['minimap']={'status':'unavailable'};png=texture()
        first=m.update(s,0,png=png);old=first['stamp']['region']
        m=SpatialModel(m.dump());self.assertEqual(m.update(s,0,png=png)['status'],'relocalization_pending')
        restored=m.update({**s,'frame_sha256':'b'},1,png=png)
        self.assertEqual(restored['stamp']['region'],old);self.assertEqual(restored['movement']['status'],'relocalized')
    def test_room_diagonal_is_short_but_corridor_never_cuts_corner(self):
        m=SpatialModel();s=state(pose=(1,1),known=[(x,y) for x in range(1,4) for y in range(1,4)],spatial_topology=8)
        s['minimap']['items']=[[2,2]];v=m.update(s,0);item=next(t for t in SpatialPlanner.candidates(v) if t['kind']=='item')
        self.assertEqual(item['path'],[[1,1],[2,2],[3,3]])
        m=SpatialModel();s=state(known=[(1,1),(2,1),(2,2)],spatial_topology=8);s['minimap']['items']=[[1,1]]
        v=m.update(s,0);item=next(t for t in SpatialPlanner.candidates(v) if t['kind']=='item')
        self.assertEqual(item['path'],[[1,1],[2,1],[2,2]])
    def test_spatial_path_controls_action_model_instead_of_unrelated_prior(self):
        from autoplayer.nextgen.model import ForwardModel
        lane=SpatialLane();s=state();lane.begin(s,0);s['spatial']=lane.merge('a',0)['view'];target=next(t for t in s['spatial']['targets'] if t['kind']=='item')
        with tempfile.TemporaryDirectory() as d:
            store=ExperienceStore(Path(d)/'s.sqlite','p','s');cap=CapabilityManager(store);model=ForwardModel(store)
            cs=[{'id':action_id([k],80),'keys':[k],'hold_ms':80,'declared_meanings':[meaning]} for k,meaning in [('r','right'),('m','menu')]]
            p={'spatial_realization':cap.realize_spatial(s,target,cs),'candidate_values':[{'id':cs[1]['id'],'value':1000}]}
            chosen,_,_=model.choose(s,cs,p);self.assertEqual(chosen['candidate']['keys'],['r'])
            s['spatial']['stamp']['revision']+=1
            chosen,_,_=model.choose(s,cs,p);self.assertEqual(chosen['candidate']['keys'],['m']);store.close()
        lane.close()
    def test_trace_links_target_input_observed_pose_and_revision(self):
        lane=SpatialLane();a=state();lane.begin(a,0);a['spatial']=lane.merge('a',0)['view'];target=next(t for t in a['spatial']['targets'] if t['kind']=='item')
        b=state('b',pose=(3,1));tr={'before':a,'intent':{'role':'move','delta':[1,0]},'accepted':True}
        lane.begin(b,1,transition=tr);b['spatial']=lane.merge('b',1)['view']
        row=trace(a,b,target,{'id':'input'}, {'os_submission':'accepted'})
        self.assertEqual(row['outcome'],'reached_position_not_goal_completion');self.assertEqual(row['target']['id'],target['id']);self.assertEqual(row['after']['revision'],2);lane.close()
    def test_blackout_same_floor_new_layout_does_not_join_regions(self):
        m=SpatialModel();s=state();old=m.update(s,0);s['spatial']=old
        fresh=state('new',pose=(6,6),known=[(6,6),(7,6)])
        v=m.update(fresh,1,{'before':s,'accepted':True,'intent':{'role':'move','delta':[1,0]},'boundary':{'blackout_seen':True}})
        self.assertNotEqual(old['stamp']['region'],v['stamp']['region'])
        self.assertNotIn('1,1',v['region_data']['cells'])
        self.assertEqual(m.update({**fresh,'frame_sha256':'next'},2)['stamp']['region'],v['stamp']['region'])
        self.assertTrue(m.portals)
    def test_known_failure_can_be_interacted_and_reprobed_without_claiming_open_door(self):
        m=SpatialModel();a=state();a['spatial']=m.update(a,0)
        tr={'before':a,'accepted':True,'hold_ms':140,'intent':{'role':'move','delta':[1,0]}}
        m.update(state('b'),1,tr);v=m.update(state('c'),2,tr)
        target=next(t for t in SpatialPlanner.candidates(v) if t['kind']=='obstacle')
        interact={'before':a,'accepted':True,'intent':{'role':'interact'},'target':target}
        after=m.update(state('d'),3,interact)
        fact=after['region_data']['obstacles']['2,1'];self.assertTrue(fact['allow_probe']);self.assertEqual(fact['source'],'HYPOTHESIS')
        self.assertEqual(after['pose'],[1,1])
    def test_num7_not_sent_for_visible_or_unchanged_previous_map(self):
        import hashlib,threading
        from types import SimpleNamespace
        from unittest.mock import Mock
        from autoplayer.nextgen.controller import NextGenController
        c=NextGenController.__new__(NextGenController);png=texture()
        c.cfg=SimpleNamespace(window_title='torneko',allowed_keys=['num7']);c.cancel=threading.Event();c.backend=Mock();c.builder=Mock()
        s=state(hashlib.sha256(png).hexdigest());s['timing']={};c.previous=None
        c.sense(png,base_state=s);c.backend.capture_map_display.assert_not_called()
        c.previous=copy.deepcopy(s);c.previous_image=png;s=copy.deepcopy(s);s['minimap']={'status':'unavailable'}
        out=c.sense(png,base_state=s);self.assertEqual(out['minimap']['status'],'retained');c.backend.capture_map_display.assert_not_called()
        c.previous_image=None;s['minimap']={'status':'unavailable'};c.backend.capture_map_display.return_value=None
        c.sense(png,base_state=s);c.backend.capture_map_display.assert_called_once()
    def test_missing_skill_and_unconfirmed_interaction_are_finitely_deferred(self):
        with tempfile.TemporaryDirectory() as d:
            store=ExperienceStore(Path(d)/'s.sqlite','p','s');p=ProgressManager(store);m=SpatialModel();v=m.update(state(pose=(3,1)),0);v['targets']=SpatialPlanner.candidates(v)
            target=p.select_spatial(v,{}, {'priorities':{'item':2,'frontier':1}});self.assertEqual(target['kind'],'item')
            for turn in range(3):p.spatial_feedback({'target':target,'receipt':{'os_submission':'accepted'},'outcome':'reached_position_not_goal_completion'},turn)
            self.assertNotEqual(p.select_spatial(v,{}, {'priorities':{'item':2,'frontier':1}})['id'],target['id'])
            v['stamp']['turn']=11;self.assertEqual(p.select_spatial(v,{}, {'priorities':{'item':2,'frontier':1}})['id'],target['id']);store.close()
    def test_disconnect_clears_spatial_visualization(self):
        from autoplayer.telemetry import RecognitionState
        r=RecognitionState();r.spatial={'provider':'measured'};r.update({'event':'disconnected'});self.assertEqual(r.spatial,{})
    def test_live_icon_disappearance_removes_item_target(self):
        m=SpatialModel();m.update(state(),0);s=state('b');s['minimap']['items']=[]
        self.assertFalse(any(t['kind']=='item' for t in SpatialPlanner.candidates(m.update(s,1))))
