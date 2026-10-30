"""Procedure ownership and evidence contracts; no fabricated gameplay environment."""
import copy,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from autoplayer.nextgen.execution import ProcedureExecutor
from autoplayer.nextgen.dungeon_observer import DungeonObserver
from autoplayer.nextgen.model import ForwardModel,action_id
from autoplayer.nextgen.store import ExperienceStore
from autoplayer.nextgen.capabilities import test_effect as effect
from autoplayer.controls import ACTIONS
from test_nextgen import measured

POLICY=json.loads((Path(__file__).parents[1]/'knowledge/torneko-execution-policy.json').read_text())
def frame(sha='a',enemies=(),hp=(15,15),food=70,kind=None):
    s=measured(sha);s.update(interface_hypothesis=kind is not None,metrics={'hp':{'values':list(hp)},'fullness':{'values':[food]}},retained={},floor_confirmation={'confirmed':1})
    s['minimap'].update(status='observed',enemies=[list(e) for e in enemies],items=[])
    s['spatial']={'stamp':{'frame':sha,'turn':0,'region':'r','revision':1},'pose':[5,5],'status':'ready','provider':'measured','map':{'cells':{f'{x},5':'OBSERVED' for x in range(3,10)},'obstacles':[]}}
    s['execution_ui']={'kind':kind,'entries':[],'selected_line':None};return s

def candidates():
    class Store:
        def load(self,*a,**k):return []
    obj=ForwardModel.__new__(ForwardModel)
    return obj.candidates(SimpleNamespace(allowed_keys=list(ACTIONS),key_bindings=ACTIONS,max_hold_ms=220))

def item(name='薬草',identity='one',line=1):return {'display_name':name,'entry_id':identity,'id':identity,'line_index':line,'page_signature':'p','selected':True,'identification_required':False}

def ui(s,kind,labels,selected=0,rows=None):
    s['interface_hypothesis']=True;s['execution_ui']={'kind':kind,'selected_line':selected,'entries':[{'line':i,'text':label,'box':[20,20+i*30,180,40+i*30]} for i,label in enumerate(labels)]}
    if rows is not None:s['execution_ui']['inventory']={'status':'observed','rows':rows,'page_signature':'p'}
    return s

class ExecutionContracts(unittest.TestCase):
    def setUp(self):self.e=ProcedureExecutor(None,ACTIONS,POLICY);self.cs=candidates()
    def choose(self,s,step=0):return self.e.choose(s,self.cs,step)
    def send(self,b,a,r,step=1,messages=()):self.e.feedback(b,a,{'keys':r['keys']},{'os_submission':'accepted'},step,messages)
    def test_adjacent_enemy_owns_action_even_at_one_hp_and_without_learned_skill(self):
        s=frame(enemies=[(0,1)],hp=(1,15));r=self.choose(s);self.assertEqual(set(r['keys']),{'num5','s'})
        self.send(s,frame('b',[(0,1)],(1,15)),r);r=self.choose(frame('c',[(0,1)],(1,15)),2)
        self.assertEqual(r['keys'],['num1']);self.assertEqual(r['task'],'combat')
    def test_diagonal_orientation_chord_exists_and_attack_not_movement(self):
        r=self.choose(frame(enemies=[(1,-1)]));self.assertEqual(set(r['keys']),{'num5','d','w'})
        self.assertIsNotNone(self.e.realize(frame(),r,self.cs))
    def test_low_hp_uses_known_heal_before_attack(self):
        row=item();self.e.pages={'p':[row]};r=self.choose(frame(enemies=[(0,1)],hp=(2,15)))
        self.assertEqual(r['task'],'heal');self.assertEqual(r['keys'],['num0'])
    def test_rest_is_one_real_input_and_stops_at_full_hp_or_approaching_enemy(self):
        r=self.choose(frame(hp=(8,15)));self.assertEqual(r['role'],'rest');self.assertEqual(r['keys'],['num6'])
        r=self.choose(frame('b',enemies=[(0,1)],hp=(8,15)),1);self.assertEqual(r['role'],'face')
        self.e.last_review=2;self.e.review_needed=False;self.assertIsNone(self.choose(frame('c'),2))
    def test_hunger_uses_food_and_missing_food_does_not_create_permanent_menu_loop(self):
        self.e.pages={'p':[item('パン')]};r=self.choose(frame(food=0));self.assertEqual(r['task'],'food')
        self.e.interrupt('test');self.e.pages={};self.e.last_review=0;self.e.review_needed=False;self.e.attempted[self.e.task_token('review',None)]=128
        self.assertIsNone(self.choose(frame(food=0),1))
    def test_startup_inventory_audit_and_no_confirm_when_selection_is_unknown(self):
        s=frame();r=self.choose(s);self.assertEqual(r['task'],'review')
        s=ui(frame('b'),'main_menu',['道具','マップ','足元'],selected=None)
        for t in range(1,6):self.assertIsNone(self.choose(s,t))
        self.assertIsNone(self.e.active);self.assertIn('selection',self.e.history[-1]['reason'])
    def test_same_named_rows_are_navigated_by_entry_not_name(self):
        target=item('薬草','second',2);self.e.begin('heal',frame(),0,'test',(target,POLICY['items'][0]))
        s=ui(frame('b'),'inventory',['道具','薬草','薬草'],selected=1,rows=[item('薬草','first',1),target])
        r=self.choose(s,1);self.assertEqual(r['keys'],['s'])
        s['execution_ui']['selected_line']=2;r=self.choose(s,2);self.assertEqual(r['keys'],['num1'])
    def test_item_popup_requires_previously_selected_instance(self):
        target=item();self.e.begin('heal',frame(),0,'test',(target,POLICY['items'][0]))
        s=ui(frame('b'),'item_actions',['飲む','投げる','置く']);self.assertIsNone(self.choose(s,1));self.assertFalse(self.e.active.get('sent'))
    def test_item_use_observes_effect_not_menu_closure(self):
        target=item();s=frame(hp=(2,15));self.e.status={'hp':{'values':[2,15]}};self.e.begin('heal',s,0,'test',(target,POLICY['items'][0]));self.e.active['item_selected']=True
        b=ui(frame('b',hp=(2,15)),'item_actions',['飲む','投げる','置く']);r=self.choose(b,1);a=frame('c',hp=(2,15));self.send(b,a,r,2)
        self.assertEqual(self.e.active['stage'],'verify');self.assertNotIn('procedure_effect',a)
        self.assertIsNone(effect(b,a,'use_item'))
        a=frame('d',hp=(12,15));r=self.choose(a,3);self.assertIsNone(r);self.assertTrue(a['procedure_effect']['verified'])
    def test_growth_requires_max_strength_or_antidote_before_seed(self):
        self.e.review_needed=False;self.e.last_review=0;self.e.pages={'p':[item('ちからの種')]};self.e.status={'strength':{'values':[6,8]}}
        self.assertIsNone(self.choose(frame(),1))
        self.e.pages['p'].append(item('毒けし草','antidote'));r=self.choose(frame(),2);self.assertEqual(self.e.active['kind'],'restore_strength')
        self.e.interrupt('test');self.e.status={'strength':{'values':[8,8]}};r=self.choose(frame(),3);self.assertEqual(self.e.active['kind'],'growth')
    def test_equipment_requires_observed_description_and_compares_status(self):
        self.e.last_review=0;self.e.review_needed=False;row=item('盾');row['equipment_estimate']={'slot':'shield','value':6,'special':False};self.e.pages={'p':[row]};self.e.status={'shield':{'values':[3]}}
        r=self.choose(frame(),1);self.assertEqual(r['task'],'equip')
        self.e.interrupt('test');row['equipment_estimate']['special']=True;self.assertIsNone(self.choose(frame(),2))
    def test_direction_two_cells_away_waits_for_contact_without_approach(self):
        s=frame(enemies=[(0,2)]);self.assertEqual(self.choose(s)['role'],'rest')
        self.assertEqual(self.choose(frame('b',[(0,1)]),1)['role'],'face')
    def test_static_enemy_with_low_hp_is_not_unconditionally_attacked(self):
        self.e.last_review=0;self.e.review_needed=False
        for t in range(3):self.e.enemies(frame(str(t),[(0,1)],(5,15)),t)
        r=self.choose(frame('x',[(0,1)],(5,15)),3);self.assertTrue(r is None or r['role']!='confirm')
    def test_input_censoring_does_not_advance_procedure(self):
        r=self.choose(frame());self.e.feedback(frame(),frame('b'),{'keys':r['keys']},{'os_submission':'censored'},1)
        self.assertIsNone(self.e.active)
    def test_deadline_and_unresponsive_inputs_are_finite(self):
        b=frame();r=self.choose(b)
        for t in range(1,5):
            if not self.e.active:break
            r=self.choose(b,t);self.send(b,b,r,t)
        self.assertIsNone(self.e.active)
    def test_full_inventory_discard_never_chooses_important_or_equipped_unknown(self):
        self.e.inventory={'occupancy':{'count':20}};one=item('盾','a');one['equipment_estimate']={'slot':'shield','value':1};two=item('盾','b');two['equipment_estimate']={'slot':'shield','value':4};self.e.pages={'p':[one,two]}
        self.assertIsNone(self.e.discard_candidate());one['equipped']=False;self.assertEqual(self.e.discard_candidate()[0]['entry_id'],'a')
        one['display_name']='箱';self.assertIsNone(self.e.discard_candidate())
    def test_ranged_uses_inventory_shoot_only_with_observed_line_of_fire(self):
        self.e.review_needed=False;self.e.last_review=0;self.e.pages={'p':[item('木の矢')]};s=frame(enemies=[(3,0)])
        self.assertTrue(self.e.clear_ray(s,self.e.enemies(s,0)[0]));r=self.choose(s,0);self.assertEqual(r['role'],'face');self.send(s,frame('b',[(3,0)]),r)
        r=self.choose(frame('c',[(3,0)]),2);self.assertEqual(r['task'],'ranged');self.assertEqual(r['keys'],['num0'])
        s['spatial']['map']['cells']={};self.assertFalse(self.e.clear_ray(s,{'delta':(3,0),'distance':3}))
    def test_battle_messages_link_to_stats_and_stale_text_does_not_count_twice(self):
        b=frame(enemies=[(1,0)]);a=frame('b');m={'kind':'damage_dealt','enemy':'スライム','damage':4,'quote':'スライムに4ポイントのダメージを与えた','source_frame_sha256':'b'}
        self.e.feedback(b,a,{'keys':['num1']},{'os_submission':'accepted'},1,[m,m]);self.assertEqual(next(iter(self.e.battles.values()))['hits'],1)
        self.assertTrue(effect(b,a,'handle_target'))
        a['quotes']=[m['quote']];self.e.feedback(a,frame('c'),{'keys':['num1']},{'os_submission':'accepted'},2,[m]);self.assertEqual(next(iter(self.e.battles.values()))['hits'],1)
    def test_death_clears_current_inventory_but_keeps_experience(self):
        self.e.pages={'p':[item()]};self.choose(frame(hp=(0,15)));self.assertEqual(self.e.pages,{})
    def test_ordinary_yes_choice_is_observed_and_protected_choice_is_not_automated(self):
        s=ui(frame(),'choice',['はい','いいえ'],1);self.assertEqual(self.choose(s)['keys'],['w'])
        s['execution_ui']['selected_line']=0;self.assertEqual(self.choose(s)['keys'],['num1'])
        s['execution_ui']['protected']=True;self.assertIsNone(self.choose(s))
    def test_execution_rejects_stale_evidence_and_overrides_unrelated_model_preference(self):
        with tempfile.TemporaryDirectory() as d:
            store=ExperienceStore(Path(d)/'s.sqlite','p','s');model=ForwardModel(store);s=frame();r=self.choose(s);real=self.e.realize(s,r,self.cs)
            plan={'execution_realization':real,'candidate_values':[{'id':next(a['id'] for a in self.cs if a['keys']==['a']),'value':1000}]}
            chosen,_,_=model.choose(s,self.cs,plan);self.assertEqual(chosen['candidate']['keys'],['num0'])
            s['frame_sha256']='changed'
            with self.assertRaises(ValueError):model.choose(s,self.cs,plan)
            store.close()
    def test_real_duplicate_ocr_adapter_exposes_distinct_selected_entry(self):
        f=json.loads((Path(__file__).parent/'fixtures/duplicate-inventory-1793097861257173000.json').read_text())['frames'][0]
        native=copy.deepcopy(f['ocr']);native['lines'][f['selected_line']]['text']='▶'+native['lines'][f['selected_line']]['text']
        s=frame(native['frame_sha256'],kind='inventory');o=DungeonObserver('s');v=o.observe(s,native)
        shields=[r for r in v['inventory']['rows'] if '青銅' in r['display_name']]
        self.assertEqual(len(shields),2);self.assertNotEqual(shields[0]['entry_id'],shields[1]['entry_id']);self.assertEqual(sum(r['selected'] for r in shields),1)
    def test_actual_low_hp_diagnostic_features_no_longer_fall_back_to_frontier(self):
        f=json.loads((Path(__file__).parent/'fixtures/execution-low-hp-1793329969730769900.json').read_text())
        s=frame();s['metrics']=f['metrics'];s['minimap']=f['minimap'];s['spatial']=f['spatial'];s['spatial']['stamp']['frame']=s['frame_sha256']
        r=self.choose(s);self.assertEqual(r['task'],'combat');self.assertEqual(r['role'],'face');self.assertNotEqual(r['keys'],f['old_input'])
    def test_description_can_supply_effect_without_inventing_item_true_name(self):
        row=item('赤い草');row['identification_required']=True;row['known_description']={'quotes':['飲むとHPが回復する。']};self.e.pages={'p':[row]}
        matched=self.e.known_item('heal');self.assertEqual(matched[0]['display_name'],'赤い草');self.assertEqual(matched[1]['basis'],'current_description_inference')
        row['known_description']={'quotes':['HPが回復するが毒でちからが下がる。']};self.assertIsNone(self.e.known_item('heal'))
    def test_identification_requires_current_explicit_target_prompt(self):
        scroll=item('インパスの巻物','scroll');target=item('赤い指輪','alias');self.e.begin('identify',frame(),0,'test',(scroll,POLICY['items'][6]));self.e.active.update(stage='identify_target',identify_target=target)
        s=ui(frame('b'),'inventory',['道具','赤い指輪'],1,rows=[target]);self.assertIsNone(self.choose(s,1))
        s['quotes']=['どの道具を識別しますか'];r=self.choose(s,2);self.assertEqual(r['keys'],['num1'])
        self.send(s,frame('c'),r,3);self.assertEqual(self.e.active['stage'],'verify')
    def test_multiple_adjacent_enemies_can_trigger_a_move_with_contact_reduction(self):
        s=frame(enemies=[(1,0),(0,1)]);s['spatial']['map']['cells'].update({'4,5':'OBSERVED','5,4':'OBSERVED'})
        r=self.choose(s);self.assertEqual(r['role'],'tactical_move')
        s=frame(enemies=[(1,0)]);self.assertNotEqual(self.choose(s)['role'],'tactical_move')
    def test_battle_estimate_conditions_recovery_on_observed_damage(self):
        self.e.battles={'x':{'status_values':{'level':None,'shield':None},'received':[5,6],'evidence':[]}}
        self.e.pages={'p':[item()]};r=self.choose(frame(enemies=[(0,1)],hp=(10,15)))
        self.assertEqual(r['task'],'heal')
    def test_disallowed_direction_binding_never_enters_any_candidate_chord(self):
        obj=ForwardModel.__new__(ForwardModel);cs=obj.candidates(SimpleNamespace(allowed_keys=['w','a','num5'],key_bindings=ACTIONS,max_hold_ms=220))
        self.assertTrue(all(set(a['keys'])<={'w','a','num5'} for a in cs))
    def test_unchanged_battle_memory_is_not_serialized_every_turn(self):
        from unittest.mock import Mock
        store=Mock();store.load.return_value=[];e=ProcedureExecutor(store,ACTIONS,POLICY)
        for n in range(20):e.save(n)
        store.put.assert_not_called();e.dirty=True;e.save(3);store.put.assert_not_called();e.save(8);store.put.assert_called_once()
        e.save(16);self.assertEqual(store.put.call_count,1);e.dirty=True;e.save(17,force=True);self.assertEqual(store.put.call_count,2)
    def test_hp_loss_overrides_static_enemy_hypothesis_even_when_message_ocr_is_missing(self):
        self.e.review_needed=False;self.e.last_review=0
        for t in range(3):self.e.enemies(frame(str(t),[(0,1)],(5,15)),t)
        before=frame('before',[(0,1)],(5,15));after=frame('after',[(0,1)],(4,15))
        self.e.feedback(before,after,{'keys':['num1']},{'os_submission':'accepted'},3,[])
        self.assertEqual(self.choose(after,4)['role'],'face')
    def test_recorded_duplicate_frames_are_one_event_group_per_input(self):
        f=json.loads((Path(__file__).parent/'fixtures/battle-frames-1793337304872969500.json').read_text(encoding='utf-8'))
        for step in (148,149):
            messages=[e['message'] for e in f['evidence'] if e['step']==step]
            self.assertEqual(len(messages),2)
            self.e.feedback(frame('before'+str(step)),frame('after'+str(step)),{'keys':['num1']},{'os_submission':'accepted'},step,messages)
        self.assertEqual(sum(b['hits'] for b in self.e.battles.values()),2)
        self.assertTrue(all(e['message']['count_basis']=='one_input_event_group_lower_bound' for b in self.e.battles.values() for e in b['evidence']))
    def test_conflicting_names_from_one_input_remain_unknown_not_two_species(self):
        messages=[{'kind':'damage_dealt','damage':4,'enemy':name,'quote':name,'source_frame_sha256':str(n)} for n,name in enumerate(('スライム','スフイム'))]
        out=self.e.group_battle_messages(messages);self.assertEqual(len(out),1);self.assertIsNone(out[0]['enemy']);self.assertEqual(len(out[0]['name_candidates']),2)
    def test_incoming_only_observations_do_not_report_zero_hit_probability(self):
        m={'kind':'damage_received','damage':2,'enemy':'スライム','quote':'受けた','source_frame_sha256':'b'}
        self.e.feedback(frame(),frame('b'),{'keys':['w']},{'os_submission':'accepted'},1,[m])
        self.assertIsNone(next(iter(self.e.battles.values()))['hit_rate_observed'])
    def test_new_inventory_evidence_invalidates_review_cooldown_but_gold_does_not(self):
        s=frame();self.e.begin('review',s,0,'test');self.e.finish('observed_partial_inventory',6,'test')
        self.assertIsNone(self.choose(frame(),20))
        a=frame('pickup');a['quotes']=['火炎草を手に入れた。'];self.e.feedback(s,a,{'keys':['w']},{'os_submission':'accepted'},25)
        self.assertEqual(self.choose(frame('next'),26)['task'],'review');generation=self.e.review_generation
        a=frame('gold');a['quotes']=['２５ゴールドを手に入れた。'];self.e.feedback(s,a,{'keys':['w']},{'os_submission':'accepted'},27)
        self.assertEqual(self.e.review_generation,generation)
    def test_observed_large_bread_name_and_description_are_food_candidates(self):
        row=item('大きいパン');self.e.pages={'p':[row]};self.assertIsNotNone(self.e.known_item('food'))
        row=item('名称不明の食料');row['known_description']={'quotes':['おなかがいっぱいになるぞ。']};self.e.pages={'p':[row]};self.assertIsNotNone(self.e.known_item('food'))
    def test_legacy_duplicated_combat_counts_are_archived_and_excluded(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'s.sqlite';store=ExperienceStore(path,'p','one')
            store.put('autonomy',{'battles':{'old':{'hits':4}}},key='execution_knowledge')
            store.put('knowledge_claims',{'id':'combat:old','source':'EXPERIENCED','status':'UNCONFIRMED','evidence':[{'frame':'a'}]},key='combat:old');store.close()
            store=ExperienceStore(path,'p','two');e=ProcedureExecutor(store,ACTIONS,POLICY)
            self.assertEqual(e.battles,{});self.assertEqual(e.legacy_battle_models,1);e.save(1,force=True);store.close()
            with sqlite3.connect(path) as db:
                record=json.loads(db.execute("SELECT data FROM knowledge_claims WHERE id='combat:old'").fetchone()[0]);self.assertEqual(record['status'],'DISPUTED')
                saved=json.loads(db.execute("SELECT data FROM autonomy WHERE key='execution_knowledge'").fetchone()[0]);self.assertEqual(saved['schema'],2)
                self.assertIsNotNone(db.execute("SELECT data FROM autonomy WHERE key='execution_knowledge_legacy'").fetchone())
    def test_disagreeing_damage_across_frames_is_unknown_not_multiple_attacks(self):
        messages=[{'kind':'damage_dealt','damage':n,'enemy':'スライム','quote':str(n),'source_frame_sha256':str(n)} for n in (4,7)]
        out=self.e.group_battle_messages(messages);self.assertEqual(len(out),1);self.assertIsNone(out[0]['damage']);self.assertEqual(out[0]['damage_candidates'],[4,7])
        self.e.feedback(frame(),frame('b'),{'keys':['num1']},{'os_submission':'accepted'},1,messages)
        row=next(iter(self.e.battles.values()));self.assertEqual(row['hits'],1);self.assertEqual(row['dealt'],[])
    def test_food_description_is_not_inventory_capacity_or_acquisition_evidence(self):
        before=frame();after=frame('b');after['quotes']=['おなかがいっぱいになるぞ。'];old=self.e.review_generation
        self.e.feedback(before,after,{'keys':['num7']},{'os_submission':'accepted'},1)
        self.assertEqual(self.e.review_generation,old);self.assertEqual(len(self.e.pickups),0)
        after=frame('c');after['quotes']=['持ち物がいっぱいで持てない。']
        self.e.feedback(before,after,{'keys':['w']},{'os_submission':'accepted'},2);self.assertGreater(self.e.review_generation,old)
    def test_persistent_native_message_not_present_in_quotes_is_not_a_new_event(self):
        m={'kind':'damage_dealt','damage':4,'enemy':'スライム','quote':'4ポイントのダメージを与えた','source_frame_sha256':'before'}
        b=frame('before');b['quotes']=[];b['battle_message_observation']=[m]
        self.e.feedback(b,frame('after'),{'keys':['num5','w']},{'os_submission':'accepted'},1,[{**m,'source_frame_sha256':'after'}])
        self.assertFalse(self.e.battles)
