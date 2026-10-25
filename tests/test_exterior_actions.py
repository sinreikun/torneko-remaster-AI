"""Action-scope contracts, without running a game."""
import json,unittest
from autoplayer.core import Settings,Decision
from autoplayer.mapless_navigation import MaplessNavigation
from autoplayer.exterior_actions import ExteriorActions
from autoplayer.active_input import ActiveInput
from autoplayer.information_exploration import InformationExploration

class ExteriorTests(unittest.TestCase):
    def make(self,cfg=None):
        cfg=cfg or Settings();nav=MaplessNavigation(cfg)
        return cfg,nav,ExteriorActions(cfg,nav)
    def decision(self,keys):return Decision('gameplay','','','',.9,'continue',keys,80,600)
    def test_scope_only_movement_and_confirm(self):
        cfg,nav,p=self.make();c=p.observe({}, {},'gameplay')
        self.assertTrue(c['active'])
        self.assertEqual(c['actions'],[['w'],['d'],['s'],['a'],['num1']])
        for key in ('num0','num2','num3','num4','num6','num7'):
            d,report=p.enforce(self.decision([key]));self.assertIn(d.keys,c['actions']);self.assertEqual(report['previous_keys'],[key])
    def test_uncertain_position_does_not_expand_actions(self):
        cfg,nav,p=self.make();nav.uncertain=True;c=p.observe({}, {},'gameplay')
        d,_=p.enforce(self.decision(['num0']));self.assertNotIn('num0',d.keys)
    def test_scope_replaces_forbidden_mixed_chords(self):
        cfg,nav,p=self.make();p.observe({}, {},'gameplay')
        for keys in (['num0','w'],['num5'],['num1','num0'],['w','d'],['num5','w'],['num5','a'],['num5','s'],['num5','d']):
            d,_=p.enforce(self.decision(keys));self.assertIn(d.keys,p.context['actions'])
    def test_dungeon_and_ocr_dropout_remain_unrestricted(self):
        cfg,nav,p=self.make();p.observe({}, {},'gameplay')
        self.assertFalse(p.observe({'metrics':{'floor':{'values':[1]}}},{},'gameplay')['active'])
        self.assertFalse(p.observe({}, {},'gameplay')['active'])
        d,_=p.enforce(self.decision(['num0']));self.assertEqual(d.keys,['num0'])
    def test_title_loading_and_results_are_not_exterior_exploration(self):
        cfg,nav,p=self.make()
        for scene in ('title','loading','defeat','victory'):
            p.observe({}, {},'gameplay');self.assertFalse(p.observe({}, {},scene)['active'])
    def test_unclassified_without_current_image_is_not_outside(self):
        cfg,nav,p=self.make();self.assertFalse(p.observe({}, {},'unknown')['active'])
    def test_unknown_current_frame_uses_restricted_exploration(self):
        cfg,nav,p=self.make();c=p.observe({}, {'frame_sha256':'a'*64,'lines':[]},'unknown')
        self.assertTrue(c['active']);self.assertNotIn(['num0'],c['actions'])
    def test_model_menu_label_without_menu_evidence_does_not_cancel_forever(self):
        cfg,nav,p=self.make();c=p.observe({}, {'frame_sha256':'a'*64,'lines':[]},'menu')
        self.assertEqual(c['mode'],'exit_exploration');self.assertNotIn(['num2'],c['actions'])
    def test_existing_menu_only_closes_no_item_experiments(self):
        cfg,nav,p=self.make();p.observe({}, {},'gameplay')
        native={'status':'ok','lines':[{'text':t} for t in ('道具','プレイ記録','倉庫','その他')]}
        c=p.observe({},native,'menu');self.assertEqual(c['actions'],[['num2']])
        d,_=p.enforce(self.decision(['num1']));self.assertEqual(d.keys,['num2'])
    def test_confirmation_preserves_cancel_safety(self):
        cfg,nav,p=self.make();p.observe({}, {},'gameplay')
        c=p.observe({}, {'lines':[{'text':'削除しますか？'}]},'dialog')
        d,_=p.enforce(self.decision(['num1']));self.assertEqual(d.keys,['num2'])
    def test_dialog_only_advances_with_confirm(self):
        cfg,nav,p=self.make();p.observe({}, {},'gameplay');self.assertEqual(p.observe({}, {'lines':[{'text':'ここから先へ進みますか？'}]},'dialog')['actions'],[['num1']])
    def test_information_and_active_fallback_share_scope(self):
        cfg,nav,p=self.make();c=p.observe({}, {},'gameplay');state={'exterior_action_policy':c}
        information=InformationExploration(cfg);rows=information.context(self.decision(['num0']),{},state)['actions']
        self.assertEqual([r['keys'] for r in rows],c['actions'])
        active=ActiveInput(cfg)
        for _ in range(16):
            d,_=active.select(self.decision(['num0']),{},state,scope_actions=c['actions'])
            self.assertIn(d.keys,c['actions']);active.sent(d,{},state)
    def test_fallback_rotates_and_never_reintroduces_blocked_menu_keys(self):
        cfg,nav,p=self.make();p.observe({}, {},'gameplay');keys=[]
        for _ in range(12):
            d,_=p.enforce(self.decision(['num0']),blocked=p.context['actions']);keys.append(tuple(d.keys));p.sent(d.keys)
        self.assertGreater(len(set(keys)),3);self.assertFalse(any('num0' in k for k in keys))
    def test_other_games_do_not_inherit_torneko_rule(self):
        cfg,nav,p=self.make(Settings(window_title='Other'));self.assertFalse(p.observe({}, {},'gameplay')['active'])

class ExteriorDispatchTests(unittest.TestCase):
    # Protocol stubs exercise the final dispatch guard, not a fictional game.
    def run_dispatch(self,stale=False,persistent=False):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from dataclasses import replace
        from autoplayer.core import Controller
        from test_agent import Backend,Script,change
        cfg=Settings(max_steps=1,fast_learning=False)
        backend=Backend()
        if stale:backend.is_current=lambda image:False
        script=Script([change(scene='gameplay',action={'keys':['num0'],'hold_ms':80,'wait_ms':0})])
        def forbidden_selector(self,decision,*args,**kwargs):return replace(decision,keys=['num0']),{'source':'protocol_fixture'}
        with tempfile.TemporaryDirectory() as t, patch('autoplayer.mapless_navigation.MaplessNavigation.propose',return_value=None),patch('autoplayer.active_input.ActiveInput.select',forbidden_selector):
            from autoplayer.memory import MemoryStore
            memory=MemoryStore(Path(t)/'memory.sqlite',cfg) if persistent else None
            try:
                controller=Controller(cfg,backend,script,lambda _:None,Path(t)/'run',memory=memory)
                controller.run()
                rows=[json.loads(line) for line in controller.journal.read_text().splitlines()]
            finally:
                if memory:memory.close()
        self.assertTrue(backend.actions);self.assertTrue(all('num0' not in keys for keys,ms in backend.actions))
        return rows
    def test_all_selectors_are_followed_by_exterior_filter(self):
        rows=self.run_dispatch()
        sent=next(r for r in rows if r.get('event')=='sent')
        self.assertIn(sent['keys'],sent['exterior_action_policy']['actions'])
    def test_fresh_frame_recovery_cannot_reintroduce_menu_input(self):
        rows=self.run_dispatch(True)
        self.assertTrue(any(r.get('revision_reason')=='exterior_action_filter' for r in rows))

    def test_persistent_policy_cannot_reintroduce_menu_input(self):
        rows=self.run_dispatch(persistent=True)
        sent=next(r for r in rows if r.get('event')=='sent')
        self.assertEqual(sent['policy_choice']['source'],'exterior_exit_exploration')

class ExteriorPlannerTests(unittest.TestCase):
    def test_planner_grammar_and_coverage_do_not_advertise_menu_actions(self):
        import hashlib
        from unittest.mock import Mock
        from types import SimpleNamespace
        from autoplayer.core import LocalLLM
        from test_agent import change
        png=b'planner-protocol-contract';client=LocalLLM(Settings())
        client.ocr=SimpleNamespace(read=Mock(return_value={'status':'ok','frame_sha256':hashlib.sha256(png).hexdigest(),'lines':[]}))
        client.observe_scene=Mock(return_value={'scene_type':'gameplay','visible_text':[],'description':'室内の移動場面。','confidence':.9})
        client._request=Mock(return_value=json.dumps(change(scene='gameplay',action={'keys':['w'],'hold_ms':80,'wait_ms':0},reason='未試行の上方向を確認する。')))
        client.decide(png,[],False)
        args=client._request.call_args.args;prompt,schema=args[2],args[3]
        expected=[['w'],['d'],['s'],['a'],['num1']]
        self.assertEqual(prompt['exterior_action_policy']['actions'],expected)
        self.assertEqual([r['keys'] for r in prompt['information_exploration']['actions']],expected)
        self.assertNotIn('num0',prompt['allowed_keys']);self.assertNotIn('num6',prompt['allowed_keys'])
        for branch in schema['oneOf']:
            if branch['properties']['status']['enum']!=['complete']:
                self.assertEqual(branch['properties']['action']['properties']['keys']['enum'],expected)
