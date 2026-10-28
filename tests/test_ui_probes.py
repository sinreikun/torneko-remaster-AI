"""Finite observation-task contracts; no game simulation."""
import copy,tempfile,unittest
from pathlib import Path
from autoplayer.nextgen.ui_probes import UIProbes
from autoplayer.nextgen.meta import MetaController
from autoplayer.nextgen.store import ExperienceStore
from test_nextgen import measured

class UIProbeContracts(unittest.TestCase):
    def state(self):
        return {**measured(),'interface_hypothesis':True,'ui_visual':{'layout':'menu-A','difference_threshold':10,'selection':{'status':'unknown'},'needs_observation':['selection_text_correspondence_unknown'],'interaction_model':{'actions':[{'id':'short','keys':['w'],'trials':99}]}}}
    short={'id':'short','keys':['w'],'hold_ms':80,'declared_meanings':['上移動']}
    long={'id':'long','keys':['w'],'hold_ms':220,'declared_meanings':['上移動']}
    def test_old_family_trials_do_not_exhaust_current_measurement(self):
        p=UIProbes();self.assertEqual([x['id'] for x in p.priors(self.state(),[self.short,self.long],{})],['short'])
    def test_hold_lengths_share_one_finite_budget(self):
        p=UIProbes();s=self.state()
        for action in (self.short,self.long):p.observe(s,action,{'status':'updated'},{'os_submission':'accepted'},{})
        self.assertFalse(p.priors(s,[self.short,self.long],{}))
    def test_new_layout_or_measurement_can_be_tested_old_scope_stays_exhausted(self):
        p=UIProbes();s=self.state()
        for _ in range(2):p.observe(s,self.short,{'status':'updated'},{'os_submission':'accepted'},{})
        changed=copy.deepcopy(s);changed['ui_visual']['layout']='menu-B';self.assertTrue(p.priors(changed,[self.short],{}));self.assertTrue(p.priors(s,[self.short],{'ui_region_ocr':1}));self.assertFalse(p.priors(s,[self.short],{}))
        changed=copy.deepcopy(s);changed['ui_visual']['grid']={'coordinate_source':'measured_contrast_components_not_ocr'};self.assertTrue(p.priors(changed,[self.short],{}))
    def test_failed_submission_cancel_and_no_ui_do_not_become_experiments(self):
        p=UIProbes();s=self.state();p.observe(s,self.short,{'status':'updated'},{'os_submission':'failed'},{})
        p.observe(s,self.short,{'status':'censored'},{'os_submission':'accepted'},{})
        self.assertFalse(p.attempts);s['ui_visual']['selection']['status']='supported_association';self.assertFalse(p.priors(s,[self.short],{}))
    def test_capacity_does_not_evict_and_restart_old_experiments(self):
        p=UIProbes({str(i):2 for i in range(256)});self.assertFalse(p.priors(self.state(),[self.short],{}));self.assertEqual(len(p.attempts),256)
    def test_meta_persists_budget_and_does_not_reset_old_history(self):
        with tempfile.TemporaryDirectory() as folder:
            store=ExperienceStore(Path(folder)/'w.sqlite','p','s')
            try:
                m=MetaController(store,'目的');s=self.state();m.prepare(s,0)
                p=m.guide({'objective':'確認','targets':[],'candidate_values':[]},s,[self.short,self.long]);self.assertTrue(any(x['id']=='short' and '再検証' in x['reason'] for x in p['candidate_values']))
                for _ in range(2):m.ui_probes.observe(s,self.short,{'status':'updated'},{'os_submission':'accepted'},m.observer.policy)
                m.save();store.queue.join();other=MetaController(store,'目的');self.assertFalse(other.ui_probes.priors(s,[self.short,self.long],other.observer.policy));self.assertEqual(s['ui_visual']['interaction_model']['actions'][0]['trials'],99)
            finally:store.close()
    def test_refuted_text_flow_gets_one_distinct_experiment_scope(self):
        p=UIProbes();s=self.state()
        for _ in range(2):p.observe(s,self.short,{'status':'updated'},{'os_submission':'accepted'},{})
        self.assertFalse(p.priors(s,[self.short],{}));changed=copy.deepcopy(s);changed['ui_visual']['interaction_model']['text_flow_suspended']=True
        self.assertTrue(p.priors(changed,[self.short],{}))
        for _ in range(2):p.observe(changed,self.short,{'status':'updated'},{'os_submission':'accepted'},{})
        self.assertFalse(p.priors(changed,[self.short],{}));self.assertFalse(p.priors(s,[self.short],{}))
