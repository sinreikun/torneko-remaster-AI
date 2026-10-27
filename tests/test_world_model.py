"""Observation identity and item evidence contracts, not a playable environment."""
import copy,io,unittest
from PIL import Image
from autoplayer.world_model import WorldModel,Inventory,VisualSense
from autoplayer.knowledge import KnowledgeEngine
from test_policy import state

def native(*text):return {'status':'ok','captured_at':1,'lines':[{'text':s} for s in text]}
def inventory(*text):return native('道具','詳細表示切替','整理','3/20',*text)
def png(color):
    im=Image.new('RGB',(128,72),color);out=io.BytesIO();im.save(out,format='PNG');return out.getvalue()

class WorldTests(unittest.TestCase):
    def test_anonymous_identity_survives_animation_and_player_motion(self):
        w=WorldModel();s=state();s['minimap']['items']=[(2,0)]
        a=w.observe(png((80,30,40)),native('HP 15/15'),s)
        b=w.observe(png((85,35,45)),native('HP 15/15'),s)
        self.assertEqual([o['id'] for o in a['objects']],[o['id'] for o in b['objects']])
        self.assertTrue(all(o['name'] is None and o['species'] is None for o in b['objects']))
        moved=copy.deepcopy(s);moved['minimap']['player_cell'][0]+=1;moved['minimap']['items']=[(1,0)]
        c=w.observe(png((80,30,40)),native('HP 15/15'),moved)
        self.assertEqual([o['id'] for o in b['objects']],[o['id'] for o in c['objects']])
    def test_model_species_and_nonhostile_claims_remain_hypotheses(self):
        w=WorldModel();s=state();s['minimap']['enemies']=[(1,0)];r=w.observe(png('black'),native(),s)
        obj=next(o for o in r['objects'] if o['legend']=='enemy_indicator')
        w.annotate({'entities':[{'object_id':obj['id'],'kind_hypothesis':'character','species_hypothesis':'商人','attitude_hypothesis':'non_hostile','facing':'ne','name_quote':'商人'}]},native())
        o=w.objects[obj['id']];self.assertIsNone(o['name']);self.assertEqual(o['attitude'],'unknown')
        self.assertNotIn('ocr_quote',o['hypotheses']);self.assertEqual(o['hypotheses']['status'],'model_hypothesis_unverified')
    def test_map_loss_does_not_erase_map_identity_but_floor_change_does(self):
        w=WorldModel();s=state();w.observe(png('black'),native(),s);old=set(w.objects)
        missing=copy.deepcopy(s);missing['minimap']={'status':'unavailable'}
        w.observe(png('black'),native(),missing);self.assertEqual(set(w.objects),old)
        other=copy.deepcopy(s);other['metrics']['floor']['values']=[2]
        w.observe(png('black'),native(),other);self.assertFalse(set(w.objects)&old)
    def test_unknown_metric_is_not_filled_from_history(self):
        w=WorldModel();s=state();w.observe(png('black'),native(),s);s=copy.deepcopy(s);s['metrics'].pop('hp')
        r=w.observe(png('black'),native(),s);self.assertNotIn('hp',r['metrics']);self.assertIsNone(r['metric_changes']['hp']['after'])
    def test_neutral_animation_does_not_become_scene_transition(self):
        v=VisualSense();a=png((10,10,10));b=png((50,50,50));v.read(a)
        r=v.read(b,a);self.assertFalse(r['scene_change']);self.assertGreater(r['natural_activity'],.9)

class ItemTests(unittest.TestCase):
    def test_display_alias_is_not_a_true_name_and_capacity_is_not_page_count(self):
        i=Inventory();r=i.observe(inventory('▶青い草','a)こん棒'))
        self.assertEqual(len(r['rows']),2);self.assertFalse(r['complete']);self.assertIsNone(r['page'])
        self.assertEqual(r['rows'][0]['display_name'],'青い草');self.assertIsNone(r['rows'][0]['true_name'])
        identifier=r['selected_id'];i.observe(native('青い草は薬草だった'))
        self.assertEqual(i.rows[0]['true_name'],'薬草');self.assertEqual(i.rows[0]['id'],identifier)
        other=Inventory();self.assertIsNone(other.observe(inventory('▶青い草'))['rows'][0]['true_name'])
    def test_duplicate_display_names_are_not_merged(self):
        i=Inventory();r=i.observe(inventory('▶青い草','a)青い草'))
        self.assertEqual(len({x['id'] for x in r['rows']}),2)
        old={x['id'] for x in r['rows']};new=i.observe(inventory('▶青い草','a)青い草'))
        self.assertFalse(old&{x['id'] for x in new['rows']})
    def test_effect_requires_selected_item_command_and_accepted_input(self):
        i=Inventory();i.observe(inventory('▶青い草'));a=state();b=copy.deepcopy(a);b['metrics']['hp']['values'][0]+=1
        row={'observed_keys':['num1'],'executed':True,'turn_kind':'normal','input_receipt':{'os_submission':'accepted'}}
        self.assertIsNone(i.learn(row,a,b,native('飲む','投げる','置く'),native(),{}))
        effect=i.learn(row,a,b,native('▶飲む','投げる','置く'),native('HPが回復した'),{})
        self.assertIn('hp',effect['metric_changes']);self.assertEqual(effect['attribution'],'temporal_association_not_causal')
        self.assertIsNone(i.learn({**row,'executed':False},a,b,native('▶飲む'),native(),{}))
    def test_equipment_delta_and_level_confound_are_separate(self):
        i=Inventory();i.observe(inventory('▶こん棒'));a=state();b=copy.deepcopy(a)
        a['metrics'].update(sword={'values':[2]},level={'values':[1]});b['metrics'].update(sword={'values':[5]},level={'values':[2]})
        row={'observed_keys':['num1'],'executed':True,'input_receipt':{'os_submission':'accepted'}}
        e=i.learn(row,a,b,native('▶装備'),native(),{});self.assertEqual(e['equipment_effect']['sword']['after'],[5]);self.assertIn('level_change',e['confounds'])
    def test_zero_observed_effect_is_not_worthless_and_stale_selection_expires(self):
        i=Inventory();i.observe(inventory('▶青い草'));s=state();row={'observed_keys':['num1'],'executed':True,'input_receipt':{'os_submission':'accepted'}}
        e=i.learn(row,s,s,native('▶飲む'),native(),{});self.assertEqual(e['utility'],'unresolved');self.assertTrue(e['not_a_failure'])
        for _ in range(4):i.observe(native())
        self.assertIsNone(i.selected)
    def test_current_equipment_stats_parse_without_invented_values(self):
        m=KnowledgeEngine().reconcile(native('ちから 8/9','剣の強さ 5','盾の強さ 3','Lv 4'))['metrics']
        self.assertEqual(m['strength']['values'],[8,9]);self.assertEqual(m['sword']['values'],[5]);self.assertEqual(m['shield']['values'],[3]);self.assertEqual(m['level']['values'],[4])
        self.assertNotIn('sword',KnowledgeEngine().reconcile(native('剣の強さは不明'))['metrics'])

    def test_item_estimate_counts_observed_zero_and_keeps_confound_out(self):
        import json,tempfile
        from pathlib import Path
        from autoplayer.core import Settings
        from autoplayer.memory import MemoryStore
        with tempfile.TemporaryDirectory() as tmp:
            memory=MemoryStore(Path(tmp)/'m.sqlite',Settings())
            try:
                w=WorldModel(memory);w.inventory.observe(inventory('▶青い草'))
                a=state();a['metrics']['hp']['values']=[10,15];b=copy.deepcopy(a);b['metrics']['hp']['values']=[11,15]
                row={'executed':True,'observed_keys':['num1'],'input_receipt':{'os_submission':'accepted'}}
                for after,natural in [(b,{}),(a,{}),(b,{'metric_changes':{'hp':'natural regeneration'}})]:
                    e=w.inventory.learn(row,a,after,native('▶飲む'),native(),natural);w.record_item_value(e)
                data=json.loads(memory.db.execute('SELECT data FROM item_use_stats').fetchone()[0])
                self.assertEqual(data['trials'],3);self.assertEqual(data['confounded_trials'],1)
                self.assertEqual(data['dimensions']['hp']['samples'],2);self.assertEqual(data['dimensions']['hp']['sum'],1)
            finally:memory.close()
    def test_menu_navigation_is_not_an_item_use(self):
        i=Inventory();i.observe(inventory('▶青い草'))
        row={'executed':True,'observed_keys':['s'],'input_receipt':{'os_submission':'accepted'}}
        self.assertIsNone(i.learn(row,state(),state(),native('▶飲む'),native(),{}))
    def test_inventory_page_coverage_is_invalidated_on_reopening(self):
        i=Inventory();i.observe(inventory('ページ 1/2','▶青い草'));r=i.observe(inventory('ページ 2/2','▶こん棒'))
        self.assertTrue(r['complete']);i.observe(native('投げる','置く','飲む'))
        self.assertFalse(i.observe(inventory('ページ 1/2','▶青い草'))['complete'])

    def test_known_room_is_lower_exploration_priority_but_remains_a_route(self):
        w=WorldModel();s=state();s['minimap']['player_cell']=[2,2]
        known={(x,y) for x in range(5) for y in range(5)}|{(5,2),(6,2)}
        s['minimap']['accumulated_cells']=[list(c) for c in known]
        r=w.observe(png('black'),native(),s)
        self.assertIn([2,2],r['exploration']['surveyed_cells']);self.assertEqual(r['exploration']['frontier_candidates'],[[6,2]]);self.assertNotIn([5,2],r['exploration']['surveyed_cells'])
        hint=w.navigation_hint(s);self.assertEqual(hint['key'],'d');self.assertEqual(hint['target_cell'],[6,2]);self.assertEqual(hint['distance'],4)
        blocked=copy.deepcopy(s);blocked['minimap']['enemies']=[(3,0)]
        self.assertEqual(w.navigation_hint(blocked)['target_kind'],'enemy')

    def test_result_then_new_adventure_discards_aliases(self):
        w=WorldModel();s=state();w.inventory.observe(inventory('▶青い草'));w.inventory.observe(native('青い草は薬草だった'))
        result=native('今回の冒険の結果','最深階 1階','スコア 10','死亡')
        missing=copy.deepcopy(s);missing['eligible']=False;missing['minimap']={'status':'unavailable'}
        w.observe(png('black'),result,missing);r=w.observe(png('black'),native(),s)
        self.assertTrue(r['new_adventure']);self.assertEqual(w.episode,1);self.assertFalse(w.inventory.identifications)

    def test_delayed_effects_cover_four_subsequent_turns_with_correct_lag(self):
        w=WorldModel();w.inventory.observe(inventory('▶青い草'));s=state();image=png('black')
        row={'executed':True,'observed_keys':['num1'],'input_receipt':{'os_submission':'accepted'}}
        first=w.feedback(s,s,row,native('▶飲む'),native(),{},image,image)
        self.assertEqual(first['delayed_effects'],[])
        lags=[]
        for _ in range(4):
            result=w.feedback(s,s,{**row,'observed_keys':['w']},native(),native(),{},image,image)
            lags.extend(e['lag_turns'] for e in result['delayed_effects'])
        self.assertEqual(lags,[1,2,3,4]);self.assertFalse(w.pending_effects)

    def test_feedback_links_pre_action_appearance_and_retrieves_battle_experience(self):
        import hashlib
        w=WorldModel();w.visual.pitch=.08
        s=state();s['minimap']['enemies']=[[1,0]];image=png((40,70,90))
        observation=w.observe(image,native(),s)
        enemy=next(o for o in observation['objects'] if o['legend']=='enemy_indicator')
        self.assertIn('appearance_memory',enemy)
        row={'step':3,'executed':True,'observed_keys':['num1'],'input_receipt':{'os_submission':'accepted'},
            'input_source_frame_sha256':hashlib.sha256(image).hexdigest(),
            'battle_observations':[{'new_message':True,'kind':'damage_dealt','enemy':'スライム','damage':4,
              'quote':'トルネコはスライムに4ポイントのダメージを与えた','source_frame_sha256':'result'}]}
        result=w.feedback(s,s,row,native(),native(),{},image,image)
        self.assertEqual(result['visual_battle_links'][0]['candidates'][0]['object_id'],enemy['id'])
        memory=w.context()['visual_battle_experience']['current_candidates'][0]
        self.assertEqual(memory['name_candidates'],{'スライム':1});self.assertFalse(memory['identity_verified'])
        self.assertIsNone(w.objects[enemy['id']]['name'])
