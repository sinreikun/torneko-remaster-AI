"""Actual binding/geometry replay; no game simulator or gameplay success claim."""
import copy,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from autoplayer.nextgen.interaction_surface import allows_field_input,corroborated_frame
from autoplayer.nextgen.controller import load_policies
from autoplayer.nextgen.dungeon_observer import DungeonObserver
from autoplayer.nextgen.execution import ProcedureExecutor
from autoplayer.nextgen.spatial import SpatialModel,SpatialLane
from autoplayer.nextgen.model import ForwardModel
from autoplayer.nextgen.store import ExperienceStore
from test_execution import frame

FIXTURE=json.loads((Path(__file__).parent/'fixtures/input-surface-1793334756633714800.json').read_text(encoding='utf-8'))

class SurfaceContracts(unittest.TestCase):
    def setUp(self):self.policy=load_policies('トルネコ')[1]
    def test_actual_saved_key_meanings_generate_cardinal_and_diagonal_orientation(self):
        cfg=SimpleNamespace(allowed_keys=FIXTURE['allowed_keys'],key_bindings=FIXTURE['key_bindings'],max_hold_ms=220)
        cs=ForwardModel.candidates(None,cfg,self.policy['roles']);e=ProcedureExecutor(None,cfg.key_bindings,self.policy)
        for delta,keys in [((-1,0),{'num5','a'}),((1,-1),{'num5','d','w'})]:
            e=ProcedureExecutor(None,cfg.key_bindings,self.policy)
            s=frame(str(delta),enemies=[delta],hp=(1,15));r=e.choose(s,cs,0)
            self.assertEqual(set(r['keys']),keys);self.assertIsNotNone(e.realize(s,r,cs));self.assertIsNone(e.requirement)
    def test_actual_colored_rectangle_without_text_is_not_interactive_ui(self):
        self.assertTrue(FIXTURE['frame_geometry']['visible'])
        self.assertFalse(corroborated_frame(FIXTURE['frame_geometry'],FIXTURE['ocr'],FIXTURE['size']))
        self.assertEqual(FIXTURE['frame_geometry']['semantic_status'],'interface_geometry_hypothesis')
    def test_colored_menu_with_spatially_matching_text_still_activates_ui(self):
        native=copy.deepcopy(FIXTURE['ocr']);native['lines'].append({'text':'道具','words':[{'box':[610,310,40,20]}]})
        self.assertTrue(corroborated_frame(FIXTURE['frame_geometry'],native,FIXTURE['size']))
    def test_passive_battle_text_keeps_geometry_but_permits_field_observation(self):
        s=frame();s['interface_hypothesis']=True;s['ui_visual']={'active':True};s['metrics']['floor']={'values':[1]}
        n={'status':'ok','frame_sha256':'a','lines':[{'text':'トルネコはスライムから2ポイントのダメージを受けた'}]}
        s['input_surface_observation']=DungeonObserver.surface(s,n)
        self.assertTrue(s['interface_hypothesis']);self.assertTrue(allows_field_input(s))
        s['frame_sha256']='b';self.assertFalse(allows_field_input(s))
    def test_choice_or_supported_selection_is_never_passive_because_of_old_combat_text(self):
        s=frame(kind='choice');s['metrics']['floor']={'values':[1]}
        n={'status':'ok','frame_sha256':'a','lines':[{'text':'トルネコはスライムから2ポイントのダメージを受けた'},{'text':'はい'},{'text':'いいえ'}]}
        s['input_surface_observation']=DungeonObserver.surface(s,n);self.assertFalse(allows_field_input(s))
        n['lines']=n['lines'][:1];s['selection_observation']={'status':'supported_association'}
        s['input_surface_observation']=DungeonObserver.surface(s,n);self.assertFalse(allows_field_input(s))
    def test_spatial_lane_joins_passive_message_without_losing_freshness_provenance(self):
        from test_spatial import state
        s=state();s['interface_hypothesis']=True;s['ui_visual']={'active':True};s['input_surface_observation']={'kind':'passive_message','frame_sha256':'a','evidence':[{'kind':'damage_received'}]}
        lane=SpatialLane()
        try:
            lane.begin(s,0);v=lane.merge('a',0)['view'];self.assertEqual(v['status'],'ready');self.assertEqual(v['provider'],'measured')
        finally:lane.close()
    def test_owned_orientation_is_not_discarded_by_geometric_ui_chord_filter(self):
        cfg=SimpleNamespace(allowed_keys=FIXTURE['allowed_keys'],key_bindings=FIXTURE['key_bindings'],max_hold_ms=220);cs=ForwardModel.candidates(None,cfg,self.policy['roles'])
        s=frame(enemies=[(-1,0)]);s['interface_hypothesis']=True;s['input_surface_observation']={'kind':'passive_message','frame_sha256':'a','evidence':[{'kind':'damage_received'}]}
        e=ProcedureExecutor(None,cfg.key_bindings,self.policy);r=e.choose(s,cs,0)
        with tempfile.TemporaryDirectory() as d:
            store=ExperienceStore(Path(d)/'s.sqlite','p','s')
            try:
                model=ForwardModel(store);chosen,_,_=model.choose(s,cs,{'execution_realization':e.realize(s,r,cs)})
                self.assertEqual(set(chosen['candidate']['keys']),{'num5','a'})
            finally:store.close()
