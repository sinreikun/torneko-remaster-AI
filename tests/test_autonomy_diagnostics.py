"""Regressions derived from 1793168221534397700 / 1793168782779281000.
No game simulator; real-run state transition shape, not gameplay success.
"""
import copy,unittest
import test_autonomy as contracts
from test_autonomy import state,result,condition,node
from autoplayer.nextgen.meta import MetaController
from autoplayer.nextgen.brief_protocol import schema_for,decode
from autoplayer.nextgen.supervisor import validate

class DiagnosticContracts(unittest.TestCase):
    setUp=contracts.AutonomyContracts.setUp
    tearDown=contracts.AutonomyContracts.tearDown
    # Inherit DB fixture only, not the parent's test methods (removed below).
    def test_mapless_unknown_scene_does_not_request_unmeasurable_movement(self):
        m=MetaController(self.store,'ゲームを進める');m.prepare(state(),0)
        self.assertEqual(m.local['capability'],'inspect')
        # Old v0.25 checkpoint must be revalidated, not trusted by its label.
        m.local['capability']='move';m.local['id']='ability:move';m.prepare(state(),1)
        self.assertEqual(m.local['capability'],'inspect')
        s=state(menu=True);s['selection_observation']={'line_index':2,'quote':'観測した項目'};m.prepare(s,2)
        self.assertEqual(m.local['capability'],'change_selection')
    def test_missing_graph_is_required_in_planning_and_scheduled_again(self):
        schema=schema_for({'A00':self.action['id']},True)
        self.assertIn('progress_nodes',schema['required']);self.assertGreaterEqual(schema['properties']['progress_nodes']['minItems'],1)
        m=MetaController(self.store,'ゲームを進める');m.prepare(state(),0)
        self.assertIn('planning_material',m.planning_context(state(),0))
        m.planning_feedback({},3);self.assertEqual(m.planning_missing,1)
        self.assertTrue(m.needs_plan(16));self.assertFalse(m.needs_plan(4))
    def test_repeated_closure_is_not_endless_progress_and_cycle_edges_are_deprioritized(self):
        m=MetaController(self.store,'ゲームを進める');a=state('open',True);b=state('closed',False)
        close={'objective':'閉じる','targets':[{'feature':'interface_open','operator':'eq','value':0}],'required_capability':'close_interface'}
        for i in range(1,25):
            before,after=(a,b) if i%2 else (b,a)
            m.recovery=None;m.accept(close,a,i)
            m.observe(before,self.action,after,result(),{'os_submission':'accepted'},i,100)
        self.assertIn('repetition_stagnation',m.failures)
        self.assertFalse(any(x['progress'] for x in list(m.recent)[-12:]))
        guided=m.guide({'objective':'fallback','targets':[],'candidate_values':[]},a,[self.action])
        self.assertLess(next(v['value'] for v in guided['candidate_values'] if v['id']==self.action['id']),0)
    def test_explicit_test_name_cannot_disagree_with_typed_success(self):
        reply={'local_objective':'interface_closed','reason':'閉じる','preferred':[],'discouraged':[],'success_test':'interface_opened','success_text':'','review_after':16}
        with self.assertRaises(ValueError):decode(reply,{'A00':self.action['id']},[self.action],state(menu=True),validate)
    def test_success_predicate_overrides_incompatible_capability_label(self):
        m=MetaController(self.store,'ゲームを進める')
        m.accept({'objective':'メニューを閉じる','targets':[{'feature':'interface_open','operator':'eq','value':0}],'required_capability':'move'},state(menu=True),1)
        self.assertEqual(m.local['capability'],'close_interface')

