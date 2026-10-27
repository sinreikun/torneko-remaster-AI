"""SQLite roundtrip and conservative visual matching, never simulated gameplay."""
import copy,sqlite3,unittest
from pathlib import Path
from types import SimpleNamespace
from autoplayer.monitor_checkpoint import MonitorCheckpoint
from autoplayer.map_sensor import MapSensor
from autoplayer.floor_navigation import FloorNavigation
from test_policy import state

class MonitorCheckpointTests(unittest.TestCase):
    def setup_data(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        png=(Path(__file__).parent/'fixtures/menu-cursor-1793055833910841700.png').read_bytes()
        s=state();s['minimap'].update(status='observed',grid_size=[54,32],player_cell=[10,10],known_cells=[[x,y] for y in range(-2,3) for x in range(-2,3)],cell_size=12.2333)
        s['floor_confirmation']={'status':'confirmed'};s['metrics']['floor']={'values':[1]}
        sensor=MapSensor();sensor.accumulate(s['minimap'],1);sensor.cells.add((0,0))
        nav=FloorNavigation();nav.observe(s);nav.visited[(9,10)]=2
        world=SimpleNamespace(last_known_metrics={'sword':{'values':[3]}},visited=set())
        c=MonitorCheckpoint(db,'profile');c.remember(s,png,sensor,nav,world);c.save()
        return db,png,s,world
    def test_restart_restores_static_floor_and_visit_history_after_match(self):
        db,png,s,world=self.setup_data();c=MonitorCheckpoint(db,'profile');sensor=MapSensor();nav=FloorNavigation()
        self.assertTrue(c.restore(s,png,sensor,nav,world));self.assertIn((0,0),sensor.cells);self.assertIn((9,10),nav.visited)
        self.assertEqual(c.status,'restored_static_map');self.assertNotIn('enemies',c.latest or {})
        self.assertEqual(s['metrics']['sword']['values'],[3]);self.assertTrue(s['metrics']['sword']['retained'])
        self.assertFalse(c.restore(s,png,sensor,nav,world))
    def test_floor_player_shape_and_different_image_reject(self):
        for field in ('floor','player','shape','image'):
            db,png,s,w=self.setup_data();c=MonitorCheckpoint(db,'profile')
            if field=='floor':s['metrics']['floor']['values']=[2]
            if field=='player':s['minimap']['player_cell']=[11,10]
            if field=='shape':s['minimap']['known_cells'].append([3,3])
            if field=='image':png=b'not an image'
            sensor=MapSensor();self.assertFalse(c.restore(s,png,sensor,FloorNavigation(),w));self.assertFalse(sensor.cells)
    def test_hidden_map_waits_profile_isolation_and_changed_floor_clears_latest(self):
        db,png,s,w=self.setup_data();c=MonitorCheckpoint(db,'profile');hidden=copy.deepcopy(s);hidden['minimap']={'status':'unavailable'}
        self.assertFalse(c.restore(hidden,png,MapSensor(),FloorNavigation(),w));self.assertIsNotNone(c.candidate)
        self.assertIsNone(MonitorCheckpoint(db,'another-profile').candidate)
        c.remember(s,png,MapSensor(),FloorNavigation(),w);hidden['metrics']['floor']['values']=[2]
        c.remember(hidden,png,MapSensor(),FloorNavigation(),w);self.assertIsNone(c.latest)
    def test_connection_preview_is_retained_and_cannot_enable_navigation(self):
        from autoplayer.telemetry import RecognitionState
        db,png,s,w=self.setup_data();c=MonitorCheckpoint(db,'profile')
        preview=c.preview(s['metrics'],png);self.assertIsNotNone(preview)
        self.assertFalse(preview['policy_state']['eligible']);self.assertTrue(preview['policy_state']['minimap']['retained'])
        ui=RecognitionState();ui.update(preview);self.assertTrue(ui.grid());self.assertIn('照合待ち',ui.phase)
        changed=copy.deepcopy(s['metrics']);changed['floor']['values']=[2];self.assertIsNone(c.preview(changed,png))
        self.assertIsNotNone(c.candidate)
    def test_corrupt_checkpoint_is_ignored(self):
        db,png,s,w=self.setup_data()
        db.execute('UPDATE monitor_checkpoint SET data=?',('[1,2,3]',))
        self.assertIsNone(MonitorCheckpoint(db,'profile').candidate)
