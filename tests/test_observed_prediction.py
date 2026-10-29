"""Missing-channel calibration, grounded IDs and conditional interface transfer."""
import copy,tempfile,unittest
from pathlib import Path
from autoplayer.nextgen.model import ForwardModel,action_id
from autoplayer.nextgen.store import ExperienceStore
from autoplayer.nextgen.supervisor import validate,SCHEMA
from autoplayer.nextgen.protocol import wire_protocol,decode_parts
from test_nextgen import measured,plan

class ObservedPredictionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=ExperienceStore(Path(self.tmp.name)/'w.sqlite','p','s');self.model=ForwardModel(self.store);self.action={'id':action_id(['a'],80),'keys':['a'],'hold_ms':80}
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def apply(self,before,after,n=1):return self.model.observe(before,self.action,after,self.model.predict(before,self.action['id']),{'os_submission':'accepted'},n)
    def test_missing_optional_ocr_channel_does_not_make_a_correct_known_effect_wrong(self):
        a=measured();a['metrics']['level']={'values':[1]};b=copy.deepcopy(a)
        self.apply(a,b)
        del b['metrics']['level'];r=self.apply(a,b,2)
        self.assertTrue(r['match']);self.assertNotIn('metric.level.0',r['evaluated_features'])
        b['metrics']['hp']['values']=[8,20];r=self.apply(a,b,3)
        self.assertFalse(r['match']);self.assertIn('metric.hp.0',r['evaluated_features'])
    def test_current_newly_visible_feature_is_unscored_not_assumed_zero(self):
        a=measured();self.apply(a,a)
        b=copy.deepcopy(a);b['metrics']['level']={'values':[2]}
        c=copy.deepcopy(b);c['metrics']['level']={'values':[3]}
        r=self.apply(b,c,2)
        self.assertTrue(r['match']);self.assertIn('metric.level.0',r['unscored_features'])
    def test_real_reported_object_name_is_not_a_valid_observed_node_id(self):
        s=measured();p=plan();p['targets']=[{'feature':'node','operator':'eq','value':'階段','weight':.5}]
        with self.assertRaises(ValueError):validate(p,[self.action],s)
        wire,aliases,schema=wire_protocol(SCHEMA,[self.action],s)
        node=next(x for x in schema['properties']['targets']['items']['anyOf'] if x['properties']['feature']['enum']==['node'])
        self.assertEqual(node['properties']['value']['enum'],[s['id']])
        p['candidate_values']=[{'id':wire[0]['id'],'value':.5,'reason':'検証する'}]
        decoded,issues=decode_parts(p,aliases,[self.action],s,validate)
        self.assertFalse(decoded['targets']);self.assertEqual(len(decoded['candidate_values']),1);self.assertTrue(issues)
    def test_no_effect_transfer_requires_repeated_observations_in_multiple_selection_states_and_is_revisable(self):
        a=measured();a.update(interface_hypothesis=True,interface_layout='same-layout')
        for n in range(1,4):self.apply(a,a,n)
        self.assertEqual(self.model.no_effect_transfer(a,self.action['id']),0)
        b=copy.deepcopy(a);b['id']='another-selection';self.apply(b,b,4)
        self.assertGreater(self.model.no_effect_transfer(b,self.action['id']),0)
        c=copy.deepcopy(b);c['id']='changed-observed-state';self.apply(b,c,5)
        self.assertEqual(self.model.no_effect_transfer(b,self.action['id']),0)
    def test_low_quality_missing_ocr_does_not_teach_cross_selection_absence(self):
        a=measured();a.update(interface_hypothesis=True,interface_layout='same-layout',quality=.45,measurement_status='unavailable')
        for n in range(1,8):
            a=copy.deepcopy(a);a['id']=str(n%2);self.apply(a,a,n)
        self.assertEqual(self.model.no_effect_transfer(a,self.action['id']),0)
