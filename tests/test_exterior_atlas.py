"""Scene-boundary, image/graph persistence and replay contracts; no demo game."""
import io,json,random,tempfile,unittest
from pathlib import Path
from PIL import Image,ImageChops,ImageDraw
from autoplayer.core import Settings
from autoplayer.exterior_atlas import ExteriorAtlas,continuity,signature,dark_signature
from autoplayer.mapless_navigation import MaplessNavigation
from autoplayer.observation import TurnObserver

def png(im):
    out=io.BytesIO();im.save(out,format='PNG');return out.getvalue()
def texture(seed):
    rng=random.Random(seed);im=Image.new('RGB',(128,72));im.putdata([tuple(rng.randrange(30,230) for _ in range(3)) for _ in range(128*72)]);return im
STATE={'exterior_action_policy':{'active':True,'location':'outside','mode':'exit_exploration'},'white_frame_evidence':{'visible':False}}
def nav():
    n=MaplessNavigation(Settings());n.prepare(STATE,{},{},0);return n
class BoundaryTests(unittest.TestCase):
    def test_camera_slide_preserves_background_correspondence(self):
        a=texture(1);b=ImageChops.offset(a,8,0)
        self.assertEqual(continuity(png(a),png(b))['status'],'continuous')
    def test_global_brightness_shift_does_not_split_textured_layout(self):
        a=texture(1);b=a.point(lambda v:min(255,v+40))
        self.assertEqual(continuity(png(a),png(b))['status'],'continuous')
    def test_different_textured_scenes_are_not_a_slide(self):self.assertEqual(continuity(png(texture(1)),png(texture(2)))['status'],'discontinuous')
    def test_flat_brightness_change_is_not_spatial_proof(self):self.assertNotEqual(continuity(png(Image.new('RGB',(128,72),'gray')),png(Image.new('RGB',(128,72),'blue')))['status'],'discontinuous')
    def test_temporal_blackout_is_retained_even_when_final_frame_visible(self):
        class Clock:
            value=0
            def __call__(self):return self.value
            def wait(self,d):self.value+=d;return False
            def is_set(self):return False
        c=Clock();o=TurnObserver();o.track_scene_boundaries=True;o.grid=lambda frame:frame
        frames=iter([[0]*768,[0]*768]+[[180]*768]*40)
        result=o.observe(lambda:next(frames),[120]*768,'unknown',c,600,clock=c)
        self.assertTrue(result.scene_boundary['dark_seen']);self.assertTrue(result.scene_boundary['dark_recovered']);self.assertEqual(result.scene_boundary['dark_samples'],2)
class AtlasTests(unittest.TestCase):
    def make(self,path=None,profile='p'):return ExteriorAtlas(path=path,profile=profile)
    def test_entry_persists_image_and_graph_and_restores_entry_not_last_pose(self):
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'atlas.sqlite';a=png(texture(1));n=nav();atlas=self.make(path)
            atlas.prepare(a,a,STATE,n);mid=atlas.map_id;n.nodes[(0,-1)]={'visits':1,'surface':'floor'};n.pose=(0,-1);atlas.close(n)
            atlas=self.make(path);n=nav();result=atlas.prepare(a,a,STATE,n)
            self.assertTrue(result['reused']);self.assertEqual(atlas.map_id,mid);self.assertEqual(n.pose,(0,0));self.assertIn((0,-1),n.nodes)
            self.assertTrue(atlas.db.execute('SELECT length(image) FROM exterior_entries_v1').fetchone()[0]);atlas.close(n)
    def test_blackout_separates_maps_without_a_unit_edge(self):
        atlas=self.make();n=nav();a,b=png(texture(1)),png(texture(2));atlas.prepare(a,a,STATE,n);old=atlas.map_id
        row={'executed':True,'input_receipt':{'os_submission':'accepted'},'observed_keys':['s'],'step':1}
        self.assertTrue(atlas.after_input(a,b,row,n,{'dark_seen':True,'dark_recovered':True}))
        self.assertEqual(n.nodes,{(0,0):{'visits':1}});self.assertEqual(n.edges,{})
        pending=json.loads(atlas.db.execute('SELECT data FROM exterior_portals_v1').fetchone()[0]);self.assertEqual(pending['status'],'awaiting_entry')
        atlas.prepare(b,b,STATE,n);self.assertNotEqual(old,atlas.map_id);self.assertEqual(n.pose,(0,0))
        portal=json.loads(atlas.db.execute('SELECT data FROM exterior_portals_v1').fetchone()[0]);self.assertEqual(portal['origin_map'],old);self.assertEqual(portal['destination_map'],atlas.map_id)
        atlas.close(n)
    def test_discontinuity_without_blackout_also_separates_segments(self):
        atlas=self.make();n=nav();a,b=png(texture(1)),png(texture(2));atlas.prepare(a,a,STATE,n)
        self.assertTrue(atlas.after_input(a,b,{'executed':True,'input_receipt':{'os_submission':'accepted'},'observed_keys':['w']},n))
        self.assertTrue(atlas.awaiting);atlas.close(n)
    def test_dark_or_unsettled_entry_is_not_reused(self):
        atlas=self.make();n=nav();a=png(texture(1));black=png(Image.new('RGB',(128,72),'black'))
        atlas.prepare(a,a,STATE,n);old_map=atlas.map_id
        self.assertFalse(atlas.prepare(black,black,STATE,n)['ready']);self.assertFalse(n.active)
        self.assertFalse(atlas.prepare(a,black,STATE,n)['ready']);self.assertTrue(atlas.prepare(a,a,STATE,n)['ready']);self.assertNotEqual(old_map,atlas.map_id);atlas.close(n)
    def test_unknown_dark_start_does_not_prevent_title_handling(self):
        atlas=self.make();n=nav();black=png(Image.new('RGB',(128,72),'black'))
        result=atlas.prepare(black,black,STATE,n)
        self.assertTrue(result['ready']);self.assertFalse(atlas.awaiting);self.assertIsNone(atlas.map_id);atlas.close(n)
    def test_arrival_dialog_can_be_advanced_before_entry_registration(self):
        atlas=self.make();n=nav();a=png(texture(1));atlas.prepare(a,a,STATE,n)
        atlas.start_boundary(a,a,n,{'trigger':'contract'})
        state={**STATE,'white_frame_evidence':{'visible':True}}
        result=atlas.prepare(a,a,state,n)
        self.assertTrue(result['ready']);self.assertTrue(result['awaiting_entry']);self.assertIsNone(atlas.map_id);self.assertFalse(n.active)
        atlas.close(n)
    def test_profile_isolation_prevents_cross_game_reuse(self):
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'atlas.sqlite';a=png(texture(1));n=nav();first=self.make(path,'a');first.prepare(a,a,STATE,n);old=first.map_id;first.close(n)
            second=self.make(path,'b');r=second.prepare(a,a,STATE,n);self.assertNotEqual(old,second.map_id);self.assertFalse(r['reused']);second.close(n)
    def test_ambiguous_identical_entrances_are_not_merged(self):
        atlas=self.make();n=nav();a=png(texture(1));atlas.prepare(a,a,STATE,n)
        row=atlas.db.execute('SELECT * FROM exterior_entries_v1').fetchone()
        atlas.db.execute('INSERT INTO exterior_entries_v1 VALUES(?,?,?,?,?,?,?)',(row[0],'other','entry',*row[3:]));atlas.db.commit()
        atlas.start_boundary(a,a,n,{'trigger':'contract'});result=atlas.prepare(a,a,STATE,n)
        self.assertFalse(result['reused']);self.assertNotEqual(atlas.map_id,'other');atlas.close(n)
    def test_menu_does_not_become_new_map(self):
        atlas=self.make();n=nav();a=png(texture(1));atlas.prepare(a,a,STATE,n);old=atlas.map_id
        im=texture(1).resize((640,360));ImageDraw.Draw(im).rectangle((40,180,600,330),fill='black',outline='white',width=3)
        row={'executed':True,'input_receipt':{'os_submission':'accepted'},'observed_keys':['num1']}
        self.assertFalse(atlas.after_input(a,png(im),row,n));self.assertEqual(old,atlas.map_id);atlas.close(n)
    def test_unexpected_origin_reset_does_not_overwrite_saved_map(self):
        atlas=self.make();n=nav();a=png(texture(1));atlas.prepare(a,a,STATE,n);old=atlas.map_id;n.nodes[(1,0)]={'visits':1};atlas.save(n)
        n.reset('manual');atlas.checkpoint(n);self.assertIsNone(atlas.map_id)
        saved=json.loads(atlas.db.execute('SELECT data FROM exterior_maps_v1 WHERE id=?',(old,)).fetchone()[0]);self.assertEqual(len(saved['cells']),2);atlas.close(n)

    def test_saved_portal_is_not_chosen_before_unvisited_directions(self):
        atlas=self.make();n=nav();a,b=png(texture(1)),png(texture(2));atlas.prepare(a,a,STATE,n);old=atlas.map_id
        row={'executed':True,'input_receipt':{'os_submission':'accepted'},'observed_keys':['w'],'step':1}
        atlas.after_input(a,b,row,n,{'dark_seen':True});atlas.prepare(b,b,STATE,n)
        atlas.start_boundary(b,a,n,{'trigger':'contract','keys':['s']});r=atlas.prepare(a,a,STATE,n)
        self.assertTrue(r['reused']);self.assertEqual(atlas.map_id,old)
        self.assertEqual(n.propose(False)[0],['d'])
        for d in ('d','s','a'):n.trials[(n.pose,d)]=3
        self.assertEqual(n.propose(False)[0],['w']);self.assertEqual(n.mode,'既知の場面切替へ');atlas.close(n)
    def test_corrupt_entry_is_ignored_without_losing_session(self):
        atlas=self.make();n=nav();a=png(texture(1));atlas.prepare(a,a,STATE,n);old=atlas.map_id
        atlas.db.execute('UPDATE exterior_entries_v1 SET signature=?',('bad-json',));atlas.db.commit()
        atlas.start_boundary(a,a,n,{'trigger':'contract'});r=atlas.prepare(a,a,STATE,n)
        self.assertFalse(r['reused']);self.assertNotEqual(old,atlas.map_id);atlas.close(n)

class AtlasIntegrationTests(unittest.TestCase):
    def test_controller_does_not_add_stair_transition_as_one_floor_step(self):
        from types import SimpleNamespace
        import hashlib
        from autoplayer.core import Controller
        from test_agent import Script,change
        a,b=png(texture(1)),png(texture(2))
        class Backend:
            def __init__(self):self.actions=[]
            def capture(self):return b if self.actions else a
            def act(self,keys,ms,stop):self.actions.append(keys);return {'os_submission':'accepted','events':[{'message':'WM_KEYDOWN'}]}
            def release_all(self):pass
            def observe_turn(self,baseline,scene,wait,stop):
                transition=bool(len(self.actions)==1 and scene!='neutral_observation')
                return SimpleNamespace(image=self.capture(),kind='normal',changed=transition,raw_change=float(transition),masked_activity=0,duration_ms=600,scene_boundary={'dark_seen':transition,'dark_recovered':transition})
        backend=Backend();script=Script([change(scene='gameplay',action={'keys':['w'],'hold_ms':80,'wait_ms':0})]*2)
        script.ocr=SimpleNamespace(read=lambda image:{'status':'ok','lines':[],'frame_sha256':hashlib.sha256(image).hexdigest()})
        with tempfile.TemporaryDirectory() as t:
            agent=Controller(Settings(max_steps=2,fast_learning=False),backend,script,lambda _:None,Path(t)/'run');agent.run()
            rows=[json.loads(line) for line in agent.journal.read_text().splitlines()];results=[r for r in rows if r.get('event')=='result']
        self.assertEqual(results[0]['movement_result']['status'],'scene_transition')
        self.assertIsNone(results[0]['movement_result']['success']);self.assertEqual(len(results[0]['mapless_navigation']['cells']),1)
        self.assertEqual(results[1]['mapless_navigation']['pose'],[0,0])
        self.assertEqual(results[1]['mapless_navigation']['atlas']['status'],'new_entry')
    def test_diagnostic_zip_contains_entry_image_and_graph(self):
        import zipfile
        from autoplayer.memory import MemoryStore
        from autoplayer.diagnostics import export_diagnostics
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);memory=MemoryStore(root/'memory.sqlite',Settings());atlas=ExteriorAtlas(memory);n=nav();a=png(texture(1))
            try:
                atlas.prepare(a,a,STATE,n)
                for seed in range(2,11):
                    b=png(texture(seed));atlas.start_boundary(a,b,n,{'trigger':'contract'});atlas.prepare(b,b,STATE,n)
                atlas.start_boundary(b,a,n,{'trigger':'contract'});atlas.prepare(a,a,STATE,n);atlas.close(n)
                export_diagnostics(root,root/'report.zip')
                with zipfile.ZipFile(root/'report.zip') as archive:
                    maps=json.loads(archive.read('exterior_maps/maps.json'));entries=json.loads(archive.read('exterior_maps/entries.json'))
                    self.assertEqual(maps[0]['map_id'],entries[0]['map_id']);self.assertTrue(archive.read(entries[0]['image']).startswith(b'\x89PNG'))
                    self.assertTrue({m['map_id'] for m in maps}<={e['map_id'] for e in entries})
            finally:memory.close()

    def test_scene_change_during_planning_discards_old_input(self):
        from test_input_attribution import BaselineDispatchTests
        backend,rows=BaselineDispatchTests().run_contract(png(texture(2)),initial_frame=png(texture(1)))
        self.assertEqual(backend.actions,[])
        self.assertTrue(any(r.get('reason')=='external_scene_boundary_before_input' for r in rows))
