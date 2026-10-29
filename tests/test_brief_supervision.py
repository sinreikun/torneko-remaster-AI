"""Finite local supervision goals, no simulated game or numeric score coercion."""
import copy,tempfile,threading,unittest
from pathlib import Path
from autoplayer.core import Settings,LocalLLM
from autoplayer.nextgen.brief_protocol import decode,schema_for,success_options,unavailable_key_hints
from autoplayer.nextgen.supervisor import SemanticSupervisor,validate
from autoplayer.nextgen.model import ForwardModel,target_score,action_id
from autoplayer.nextgen.store import ExperienceStore
from test_nextgen import measured,plan

class BriefTests(unittest.TestCase):
    def setUp(self):
        self.state=measured();self.state['interface_hypothesis']=True
        self.actions=[{'id':action_id(['a'],80),'keys':['a'],'hold_ms':80}];self.aliases={'A00':self.actions[0]['id']}
        self.reply={'local_objective':'不要な画面を閉じて現在地を確認する','reason':'現在の表示だけでは位置が分からない','preferred':['A00'],'discouraged':[],'success_test':'interface_closed','success_text':'','review_after':16}
    def test_grounded_success_choice_couples_test_and_nonempty_text(self):
        self.state['quotes']=['道具','道具','観測原文']
        self.state['ui_visual']={'elements':[{'id':'item','text':'道具','bbox':[.1,.1,.3,.16]},{'id':'item','text':'道具','bbox':[.1,.1,.3,.16]}],
            'interaction_model':{'selection_requirement':'selection_capable'},'selection':{'status':'supported_association','element_id':'item'}}
        choices=success_options(self.state);self.state['success_choices']=choices
        choice=next(x for x in choices if x['test']=='selection_matches')
        self.assertEqual(choice['text'],'道具')
        self.assertEqual(sum(x['test']=='selection_matches' for x in choices),1)
        reply={k:v for k,v in self.reply.items() if k not in ('success_test','success_text')}
        reply['success_choice']=choice['id']
        plan,issues=decode(reply,self.aliases,self.actions,self.state,validate)
        self.assertFalse(issues);self.assertEqual(plan['targets'][0]['value'],'道具')
        schema=schema_for(self.aliases,success_choices=choices)
        self.assertNotIn('success_text',schema['properties']);self.assertIn(choice['id'],schema['properties']['success_choice']['enum'])
        with self.assertRaises(ValueError):decode({**reply,'success_choice':'missing'},self.aliases,self.actions,self.state,validate)
        with self.assertRaises(ValueError):decode({**reply,'success_text':''},self.aliases,self.actions,self.state,validate)
    def test_unknown_dialogue_does_not_offer_selection_of_sentences(self):
        self.state['quotes']=['文章だけ']
        self.state['ui_visual']={'elements':[{'text':'文章だけ','bbox':[.1,.1,.3,.16]}]}
        choices=success_options(self.state)
        self.assertNotIn('selection_matches',[c['test'] for c in choices])
        self.assertTrue(any(c['test']=='text_disappears' for c in choices))

    def test_menu_does_not_turn_hud_into_selection_targets(self):
        self.state['quotes']=['満腹度','道具','マップ']
        self.state['interface_observation']={'visible':True,'rectangles':[[.05,.2,.6,.6]]}
        self.state['ui_visual']={'elements':[{'id':'hud','text':'満腹度','bbox':[.4,.05,.6,.1]},
            {'id':'item','text':'道具','bbox':[.1,.3,.2,.35]},{'id':'map','text':'マップ','bbox':[.3,.3,.4,.35]}],
            'interaction_model':{'selection_requirement':'selection_capable'},
            'graph':[{'kind':'selection_movement_hypothesis','origin':'item','destination':'map','support':2,'confidence':.75}]}
        choices=success_options(self.state)
        self.assertNotIn('満腹度',[x['text'] for x in choices])
        self.assertEqual({x['text'] for x in choices if x['test']=='selection_matches'},{'道具','マップ'})
        self.state['ui_visual']['graph'][0]['counterexamples']=1
        self.assertFalse(any(x['test']=='selection_matches' for x in success_options(self.state)))

    def test_small_reply_selects_allowed_input_and_observable_completion(self):
        decoded,issues=decode(self.reply,self.aliases,self.actions,self.state,validate)
        self.assertFalse(issues);self.assertEqual(decoded['candidate_values'][0]['id'],self.actions[0]['id'])
        self.assertEqual(target_score(self.state,decoded['targets'][0]),0)
        closed=copy.deepcopy(self.state);closed['interface_hypothesis']=False
        self.assertEqual(target_score(closed,decoded['targets'][0]),1)
        self.assertNotIn('candidate_values',schema_for(self.aliases)['properties'])
    def test_no_unknown_or_conflicting_keys_and_no_duration_as_priority(self):
        for changed in ({'preferred':['A99']},{'preferred':[80]},{'discouraged':['A00']}):
            reply={**self.reply,**changed}
            with self.assertRaises(ValueError):decode(reply,self.aliases,self.actions,self.state,validate)
    def test_reached_visible_text_does_not_reward_staying_in_menu(self):
        with tempfile.TemporaryDirectory() as folder:
            store=ExperienceStore(Path(folder)/'w.sqlite','p','s');model=ForwardModel(store)
            try:
                a=copy.deepcopy(self.state);a['quotes']=['道具','せってい'];b=copy.deepcopy(a);b['quotes']=[];b['id']='closed';b['interface_hypothesis']=False
                action=self.actions[0]
                model.observe(a,action,b,model.predict(a,action['id']),{'os_submission':'accepted'},1)
                p=plan();p['targets']=[{'feature':'text','operator':'contains','value':'道具','weight':1}]
                selected,_,_=model.choose(a,self.actions,p)
                self.assertEqual(selected['expected_goal_value'],0)
            finally:store.close()
    def test_completion_is_recorded_when_interface_scope_changes(self):
        cfg=Settings(window_title='generic',allowed_keys=['a']);events=[]
        with tempfile.TemporaryDirectory() as folder:
            store=ExperienceStore(Path(folder)/'w.sqlite','p','s');model=ForwardModel(store);sup=SemanticSupervisor(cfg,LocalLLM(cfg),events.append,threading.Event());sup.last=0
            opened=copy.deepcopy(self.state);opened['interface_layout']='menu'
            p,_=decode(self.reply,self.aliases,self.actions,opened,validate)
            scope=sup.scope(opened,0,{})
            sup.results.put({'scope':scope,'state':opened,'plan':p,'cancelled':False,'ms':1})
            try:
                sup.tick(opened,self.actions,1,0,{},None,model,{})
                closed=copy.deepcopy(opened);closed['interface_hypothesis']=False
                sup.tick(closed,self.actions,2,0,{},None,model,{})
                goal=next(e for e in events if e['event']=='supervisor_local_goal')
                self.assertEqual(goal['status'],'observed_local_condition')
            finally:sup.close();store.close()
    def test_unavailable_screen_key_is_reported_without_authorizing_it(self):
        hints=unavailable_key_hints(['フレームレート','Tab','反映'],['a'])
        self.assertEqual(hints[0]['key'],'tab');self.assertEqual(hints[0]['status'],'screen_key_hint_not_authorized')
        self.assertFalse(unavailable_key_hints(['Tab','反映'],['tab']))
    def test_numeric_increase_uses_only_current_observed_metric(self):
        reply={**self.reply,'success_test':'metric_increases','success_text':'metric.hp.0'}
        p,_=decode(reply,self.aliases,self.actions,self.state,validate)
        self.assertEqual(p['targets'][0]['value'],11)
        reply['success_text']='metric.hidden.0'
        with self.assertRaises(ValueError):decode(reply,self.aliases,self.actions,self.state,validate)

    def test_additional_game_key_requires_explicit_allow_list_and_binding(self):
        base=Settings(allowed_keys=['w','tab']).validate()
        self.assertNotIn('tab',base.allowed_keys)
        declared=Settings(allowed_keys=['w','tab'],key_bindings={'tab':'画面設定の反映'}).validate()
        self.assertIn('tab',declared.allowed_keys)
        self.assertFalse(unavailable_key_hints(['Tab','反映'],declared.allowed_keys))

    def test_missing_ocr_or_ambiguous_frame_cannot_complete_a_disappearance_goal(self):
        reply={**self.reply,'success_test':'text_disappears','success_text':'せってい'}
        p,_=decode(reply,self.aliases,self.actions,self.state,validate)
        missing=copy.deepcopy(self.state);missing['quotes']=[];missing['measurement_status']='unavailable'
        self.assertIsNone(target_score(missing,p['targets'][0]))
        p,_=decode(self.reply,self.aliases,self.actions,self.state,validate)
        missing['interface_observation']={'visible':None};missing['interface_hypothesis']=False
        self.assertIsNone(target_score(missing,p['targets'][0]))
