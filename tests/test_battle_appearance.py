"""Numeric appearance and explicit-message association contracts; no game."""
import copy,sqlite3,unittest
from types import SimpleNamespace
from autoplayer.battle_appearance import BattleAppearanceMemory

def obj(id='object_1',label='appearance_1',offset=None):
    return {'id':id,'legend':'enemy_indicator','name':None,'offset':offset or [1,0],
      'appearance_memory':{'class_id':label,'similar_class_candidate':None,'identity_verified':False},
      'screen_association':{'status':'hypothesis','bbox':[20,20,40,40]},'appearance_variant_count':3}
def row(step=1,kind='damage_dealt'):
    return {'step':step,'executed':True,'observed_keys':['num1'],'input_receipt':{'os_submission':'accepted'},
      'input_source_frame_sha256':'before','combat_message_ocr':{'session':'s'},
      'battle_observations':[{'kind':kind,'enemy':'スライム','damage':7,'quote':'トルネコはスライムに7ポイントのダメージを与えた',
          'new_message':True,'source_frame_sha256':'after'}]}

class BattleAppearanceTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.memory=BattleAppearanceMemory(self.db,'p');self.bank=SimpleNamespace(samples=[('appearance_1','unknown',[.1]*192)])
        self.before={'metrics':{'hp':{'values':[15,15]},'level':{'values':[2]},'sword':{'values':[3]}}}
        self.observation={'frame_sha256':'before','objects':[obj()]}
    def test_visual_features_name_damage_and_conditions_survive_reload(self):
        r=row();links=self.memory.record(self.observation,r,self.before,self.before,self.bank)
        self.assertEqual(links[0]['candidates'][0]['descriptor'],[.1]*192)
        self.assertEqual(links[0]['message']['enemy'],'スライム');self.assertFalse(links[0]['identity_verified'])
        self.assertIsNone(self.observation['objects'][0]['name'])
        ctx=BattleAppearanceMemory(self.db,'p').context([obj()])['current_candidates'][0]
        self.assertEqual(ctx['name_candidates'],{'スライム':1})
        damage=ctx['conditional_battle_experience'][0]
        self.assertEqual(damage['mean_damage'],7);self.assertEqual(damage['conditions']['level']['values'],[2])
        self.assertEqual(self.memory.record(self.observation,r,self.before,self.before,self.bank),[])
        changed=copy.deepcopy(self.before);changed['metrics']['level']['recognition_at']=999
        later=row(2);later['battle_observations'][0]['damage']=5
        self.memory.record(self.observation,later,changed,changed,self.bank)
        pooled=self.memory.context([obj()])['current_candidates'][0]['conditional_battle_experience']
        self.assertEqual(len(pooled),1);self.assertEqual(pooled[0]['samples'],2);self.assertEqual(pooled[0]['mean_damage'],6)
    def test_multiple_enemies_are_not_given_the_same_confirmed_name(self):
        observation={**self.observation,'objects':[obj(),obj('object_2','appearance_2',[-1,0])]}
        links=self.memory.record(observation,row(),self.before,self.before,self.bank)
        self.assertEqual(links[0]['status'],'ambiguous_visual_candidates')
        self.assertEqual(self.memory.context([obj()])['current_candidates'][0]['name_candidates'],{})
    def test_dispatched_attack_target_narrows_outgoing_but_not_incoming(self):
        observation={**self.observation,'objects':[obj(),obj('object_2','appearance_2',[-1,0])]}
        r=row();r['combat_experiment']={'stage':'attack_trial','keys':['num1'],'target':[1,0]}
        a=self.memory.record(observation,r,self.before,self.before,self.bank)
        self.assertEqual(a[0]['candidates'][0]['object_id'],'object_1');self.assertEqual(len(a[0]['candidates']),1)
        r=row(2,'damage_received');r['combat_experiment']={'stage':'attack_trial','keys':['num1'],'target':[1,0]}
        b=self.memory.record(observation,r,self.before,self.before,self.bank)
        self.assertEqual(len(b[0]['candidates']),2)
    def test_missing_features_stale_frames_and_old_messages_do_not_teach_names(self):
        r=row();r['input_source_frame_sha256']='other'
        a=self.memory.record(self.observation,r,self.before,self.before,self.bank)
        self.assertEqual(a[0]['status'],'visual_evidence_unavailable');self.assertFalse(a[0]['visual_frame_current'])
        r=row(2);r['battle_observations'][0]['new_message']=False
        self.assertEqual(self.memory.record(self.observation,r,self.before,self.before,self.bank),[])
        a=self.memory.record(self.observation,row(3),self.before,self.before,SimpleNamespace(samples=[]))
        self.assertEqual(a[0]['status'],'visual_evidence_unavailable')
        self.assertFalse(self.memory.context([obj()])['current_candidates'][0]['name_candidates'])
    def test_similar_animation_appearance_retrieves_hypothesis_not_identity(self):
        self.memory.record(self.observation,row(),self.before,self.before,self.bank)
        variant=obj(label='appearance_new');variant['appearance_memory']['similar_class_candidate']='appearance_1'
        c=self.memory.context([variant])['current_candidates'][0]
        self.assertEqual(c['name_candidates'],{'スライム':1});self.assertFalse(c['identity_verified'])
    def test_diagnostic_and_unaccepted_inputs_never_add_experience(self):
        for patch in ({'event':'diagnostic_input'},{'dry_run':True},{'executed':False},{'input_receipt':{'os_submission':'rejected'}}):
            r=row();r.update(patch);self.assertEqual(self.memory.record(self.observation,r,self.before,self.before,self.bank),[])
