"""Numerical graph and observation contracts. No game or simulated gameplay."""
import copy,json,unittest
from pathlib import Path
from autoplayer.floor_navigation import FloorNavigation
from autoplayer.telemetry import RecognitionState
from test_policy import state,receipt_row

def observed(pos,known,floor=1,stairs=(),items=(),enemies=()):
    s=state();s['metrics']['floor']['values']=[floor]
    s['minimap'].update(player_cell=list(pos),grid_size=[54,32],accumulated_cells=[list(c) for c in known],known_cells=[],
      stairs=[[x-pos[0],y-pos[1]] for x,y in stairs],items=[[x-pos[0],y-pos[1]] for x,y in items],enemies=[[x-pos[0],y-pos[1]] for x,y in enemies])
    return s

class FloorNavigationTests(unittest.TestCase):
    def test_remote_frontier_survives_new_nearby_frontier_and_menu(self):
        known={(x,2) for x in range(1,9)};nav=FloorNavigation();start=observed((2,2),known)
        nav.observe(start);nav.visited[(1,2)]=1
        for key in ('a','w','s'):nav.failures[(1,2),key]=1
        first=nav.hint(start);self.assertEqual(first['target_cell'],[8,2])
        expanded=known|{(3,1)};next_state=observed((3,2),expanded)
        self.assertEqual(nav.hint(next_state)['target_cell'],[8,2])
        saved=copy.deepcopy(nav.exploration_task)
        self.assertIsNone(nav.hint({'eligible':False,'minimap':{},'metrics':{}}))
        self.assertEqual(nav.exploration_task,saved)
        nav.hint(observed((3,2),expanded,floor=2))
        self.assertNotEqual(nav.exploration_task,saved)

    def test_higher_priority_item_interrupts_then_resumes_remote_task(self):
        known={(x,2) for x in range(1,9)};nav=FloorNavigation();start=observed((2,2),known)
        nav.observe(start);nav.visited[(1,2)]=1
        for key in ('a','w','s'):nav.failures[(1,2),key]=1
        self.assertEqual(nav.hint(start)['target_cell'],[8,2])
        item=observed((3,2),known,items=[(2,2)])
        self.assertEqual(nav.hint(item)['target_kind'],'items')
        self.assertEqual(nav.exploration_task['status'],'interrupted')
        self.assertEqual(nav.exploration_task['target_cell'],[8,2])
        nav.item_tasks.decide([{'cell':[2,2],'decision':'defer','reason':'明示的に保留','revisit':'inventory_changed'}])
        self.assertEqual(nav.hint(observed((3,2),known))['target_cell'],[8,2])
        self.assertEqual(nav.exploration_task['status'],'navigating')

    def test_blocked_remote_task_seeks_target_side_boundary_without_unblocking_edge(self):
        known={(x,2) for x in range(1,9)};nav=FloorNavigation();s=observed((2,2),known)
        nav.observe(s);nav.visited[(1,2)]=1
        for key in ('a','w','s'):nav.failures[(1,2),key]=1
        self.assertEqual(nav.hint(s)['target_cell'],[8,2])
        nav.failures[(4,2),'d']=2
        hint=nav.hint(s)
        self.assertEqual(hint['target_kind'],'boundary_recheck')
        self.assertEqual(hint['target_cell'],[4,2])
        self.assertEqual(nav.exploration_task['target_cell'],[8,2])
        self.assertEqual(nav.exploration_task['status'],'detour_search')
        self.assertIsNone(nav._path({(8,2)}))
        self.assertEqual(nav.context()['suppressed_edges'],[{'origin':[4,2],'keys':['d'],'failures':2}])
        # New accepted successful traversal, not elapsed ticks, reopens the edge.
        nav.record(observed((4,2),known),observed((5,2),known),receipt_row('d'))
        self.assertEqual(nav.hint(observed((5,2),known))['target_cell'],[8,2])
        self.assertEqual(nav.exploration_task['status'],'navigating')

    def test_supplied_topology_keeps_distant_frontier_without_missing_failure_history(self):
        data=json.loads((Path(__file__).parent/'fixtures/remote-frontier-1792987820475716400.json').read_text())
        nav=FloorNavigation();s=data['state'];nav.observe(s)
        nav.visited.update({tuple(p):1 for p in data['visited_cells']})
        self.assertEqual(nav.primary(),{(32,27)})
        # Export lacks suppressed edge origins; do not manufacture that history.
        self.assertEqual(data['reported_suppressed_edge_count'],1)
        self.assertEqual(nav.hint(s)['target_cell'],[32,27])
        for logged in data['logged_choices']:
            updated=copy.deepcopy(s);old=s['minimap']['player_cell'];pos=logged['position']
            updated['minimap']['player_cell']=pos
            for field in ('known_cells','stairs','enemies','items','uncertain_marks'):
                updated['minimap'][field]=[[x+old[0]-pos[0],y+old[1]-pos[1]] for x,y in s['minimap'].get(field,[])]
            hint=nav.hint(updated)
            self.assertEqual(hint['target_cell'],[32,27])
            self.assertEqual(nav.exploration_task['target_cell'],[32,27])

    def test_dead_end_is_retired_but_floor_remains_a_return_route(self):
        known={(x,4) for x in range(40,45)};nav=FloorNavigation();s=observed((43,4),known)
        nav.observe(s);end=observed((44,4),known);nav.record(s,end,receipt_row('d'))
        for key in ('d','w','s'):nav.record(end,end,receipt_row(key))
        hint=nav.hint(end,'frontier');self.assertEqual(hint['key'],'a');self.assertEqual(hint['target_cell'],[40,4])
        back=observed((43,4),known);nav.record(end,back,receipt_row('a'))
        hint=nav.hint(back,'frontier');self.assertEqual(hint['key'],'a');self.assertNotEqual(hint['target_cell'],[44,4])
        self.assertIn([44,4],nav.context()['exhausted_branch_cells']);self.assertIn((44,4),nav.known)
        # A later objective can require crossing a completed branch.
        stairs=observed((43,4),known,stairs=[(44,4)])
        hint=nav.hint(stairs,'stairs');self.assertEqual(hint['key'],'d');self.assertEqual(hint['target_kind'],'stairs')
    def test_unknown_probes_are_bounded_not_permanent_walls(self):
        known={(1,1),(2,1),(3,1)};nav=FloorNavigation();s=observed((3,1),known)
        nav.observe(s)
        for key in ('w','d','s'):nav.record(s,s,receipt_row(key))
        self.assertFalse(nav.unknown_keys((3,1)))
        grown=observed((3,1),known|{(3,0)})
        nav.observe(grown);self.assertIn('d',nav.unknown_keys((3,1)))
        self.assertEqual(nav.failures[(3,1),'w'],0)
    def test_enemy_neutral_backend_and_wrong_key_do_not_complete_probe(self):
        known={(1,1),(2,1)}
        cases=[(receipt_row('num1'),{},()),(receipt_row('w'),{'position':1},()),
          (receipt_row('w',executed=False),{},()),(receipt_row('w',input_receipt={'os_submission':'rejected'}),{},()),
          (receipt_row('w'),{},[(2,0)])]
        for row,natural,enemies in cases:
            nav=FloorNavigation();s=observed((2,1),known,enemies=enemies);nav.observe(s);nav.record(s,s,row,natural)
            self.assertEqual(nav.failures[(2,1),'w'],0)
    def test_menu_and_pending_floor_retain_but_confirmed_floor_resets(self):
        nav=FloorNavigation();s=observed((2,1),{(1,1),(2,1)});nav.observe(s)
        nav.record(s,s,receipt_row('d'));before=nav.context()
        menu={'eligible':False,'minimap':{'status':'unavailable'},'metrics':{}}
        self.assertIsNone(nav.hint(menu));self.assertEqual(nav.context(),before)
        pending=copy.deepcopy(s);pending['metrics']['floor']['values']=[2];pending['floor_confirmation']={'status':'pending'}
        self.assertFalse(nav.observe(pending));self.assertEqual(nav.floor,(1,))
        nav.observe(observed((9,9),{(9,9)},floor=2));self.assertEqual(nav.floor,(2,));self.assertNotIn((2,1),nav.visited)
    def test_stairs_default_and_model_objectives_are_distinct(self):
        known={(x,1) for x in range(1,6)};s=observed((3,1),known,stairs=[(5,1)],items=[(2,1)])
        nav=FloorNavigation();self.assertEqual(nav.hint(s)['key'],'a');self.assertEqual(nav.hint(s)['target_kind'],'items')
        self.assertEqual(nav.hint(s,mission='collect_visible_items',plan_id='p1')['key'],'a')
        nav.hint(s);self.assertEqual(nav.goal['mission'],'collect_visible_items')
        s['minimap']['items']=[]
        nav.item_tasks.decide([{'cell':[2,1],'decision':'defer','reason':'帰還のため明示的に保留','revisit':'inventory_changed'}])
        self.assertEqual(nav.hint(s,mission='return_with_objective',plan_id='p2')['key'],'d')
        self.assertEqual(nav.goal['phase'],'return_to_stairs')
    def test_stair_trial_does_not_claim_progress_or_repeat_forever(self):
        s=observed((2,1),{(1,1),(2,1)},stairs=[(2,1)]);nav=FloorNavigation()
        for _ in range(2):
            self.assertEqual(nav.hint(s,'stairs')['key'],'num1');nav.record(s,s,receipt_row('num1'))
        self.assertIsNone(nav.hint(s,'stairs'));self.assertTrue(nav.goal['needs_review']);self.assertEqual(nav.goal['phase'],'verify_stair_action')
    def test_enemy_blocks_route_without_retargeting_room_corners(self):
        known={(x,y) for x in range(5) for y in range(5)}|{(5,2),(6,2)}
        nav=FloorNavigation();s=observed((2,2),known,enemies=[(5,2)])
        self.assertEqual(nav.hint(s)['target_kind'],'enemy');self.assertEqual(nav.hint(s)['target_cell'],[4,2]);s['minimap']['enemies']=[];self.assertEqual(nav.hint(s)['key'],'d')
    def test_monitor_marks_are_retained_in_menu_and_icons_win(self):
        nav=FloorNavigation();s=observed((2,1),{(1,1),(2,1)},stairs=[(1,1)]);nav.hint(s)
        s['floor_exploration']=nav.context();monitor=RecognitionState();monitor.update({'event':'perception','policy_state':s})
        self.assertEqual(monitor.grid()[(2,1)],'player');self.assertEqual(monitor.grid()[(1,1)],'stairs')
        monitor.update({'event':'perception','policy_state':{'minimap':{'status':'unavailable'},'metrics':{}}})
        self.assertEqual(monitor.floor_exploration,s['floor_exploration']);self.assertIn('階内目標',monitor.summary())

    def test_supplied_real_log_no_longer_targets_completed_end(self):
        rows=json.loads((Path(__file__).parent/'fixtures/floor-dead-end-1792936483410881100.json').read_text())['rows']
        nav=FloorNavigation()
        for row in rows:
            nav.observe(row['before']);nav.record(row['before'],row['after'],row)
        last=rows[-1]['after'];hint=nav.hint(last,'frontier')
        self.assertEqual(last['minimap']['player_cell'],[43,4])
        self.assertEqual(hint['key'],'a');self.assertNotEqual(hint['target_cell'],[44,4])
        self.assertIn([44,4],nav.context()['exhausted_branch_cells'])
        item=nav.hint(last)
        self.assertEqual(item['target_kind'],'items');self.assertNotEqual(item['target_cell'],[44,4])

    def test_missing_floor_cells_do_not_invent_a_navigation_goal(self):
        nav=FloorNavigation();s=state()
        self.assertIsNone(nav.hint(s));self.assertEqual(nav.context()['status'],'unavailable')

    def test_enemy_then_visible_item_then_frontier_including_visited_items(self):
        known={(x,2) for x in range(1,8)};nav=FloorNavigation()
        s=observed((3,2),known,enemies=[(7,2)],items=[(2,2)])
        self.assertEqual(nav.hint(s)['target_kind'],'enemy');self.assertEqual(nav.hint(s)['key'],'d')
        nav.visited[(2,2)]=4;s['minimap']['enemies']=[]
        self.assertEqual(nav.hint(s)['target_kind'],'items');self.assertEqual(nav.hint(s)['key'],'a')
        arrived=observed((2,2),known,items=[(2,2)])
        nav.record(s,arrived,receipt_row('a'))
        self.assertNotEqual((nav.hint(arrived) or {}).get('target_kind'),'items')
        nav.inventory_updated('old');nav.inventory_updated('changed')
        self.assertEqual(nav.item_attempts[(2,2)],0)

    def test_supplied_item_log_prefers_item_over_frontier(self):
        data=json.loads((Path(__file__).parent/'fixtures/item-priority-1792956399741382900.json').read_text())
        hint=FloorNavigation().hint(data['state'])
        self.assertEqual(hint['target_kind'],'items');self.assertEqual(hint['distance'],3)
        self.assertTrue(any(abs(b[0]-a[0])==abs(b[1]-a[1])==1 for a,b in zip(hint['route_cells'],hint['route_cells'][1:])))

    def test_goal_route_uses_shorter_diagonal_and_records_result(self):
        known={(x,y) for x in range(1,4) for y in range(1,4)};nav=FloorNavigation()
        s=observed((1,1),known,items=[(3,3)])
        h=nav.hint(s);self.assertEqual(h['keys'],['s','d']);self.assertEqual(h['distance'],2)
        after=observed((2,2),known,items=[(3,3)]);r=receipt_row('s',observed_keys=['s','d'])
        nav.record(s,after,r);self.assertTrue(r['floor_navigation_outcome']['moved'])
        self.assertEqual(r['floor_navigation_outcome']['arrival_visits_before'],0)
        self.assertIn([2,2],nav.context()['visited_cells'])
    def test_diagonal_does_not_cut_unknown_corners_or_become_an_aimless_probe(self):
        for known in ({(1,1),(2,2)},{(1,1)}):
            h=FloorNavigation().hint(observed((1,1),known,items=[(2,2)] if len(known)>1 else []))
            self.assertIsNotNone(h);self.assertNotIn('+',h['key']);self.assertEqual(h['mode'],'frontier_probe')

    def test_stale_cached_frontier_in_supplied_log_is_not_retargeted(self):
        rows=json.loads((Path(__file__).parent/'fixtures/stale-frontier-1792958227411834200.json').read_text())['rows']
        nav=FloorNavigation()
        for r in rows:
            nav.observe(r['before']);nav.record(r['before'],r['after'],r)
            hint=nav.hint(r['after'])
            self.assertIn((14,16),nav.disputed())
            self.assertNotEqual((hint or {}).get('target_cell'),[14,16])
            self.assertNotIn([14,16],(hint or {}).get('route_cells',[]))
        self.assertIn([14,16],nav.context()['disputed_cached_cells'])
        self.assertIn((14,16),nav.known) # History remains; it is not a wall declaration.
    def test_failed_edge_is_not_reenabled_by_observation_ticks(self):
        known={(1,1),(2,1),(3,1)};nav=FloorNavigation();s=observed((2,1),known,stairs=[(3,1)])
        nav.observe(s)
        for _ in range(2):nav.record(s,s,receipt_row('d'))
        for _ in range(40):nav.observe(s)
        self.assertIsNone(nav._path({(3,1)}))
        self.assertIn(['d'],nav.context()['ineffective_actions_here'])
        arrived=observed((3,1),known,stairs=[(3,1)]);nav.record(s,arrived,receipt_row('d'))
        self.assertEqual(nav.failures[(2,1),'d'],0)
    def test_only_independent_current_maps_dispute_cache_and_fresh_floor_restores_it(self):
        s=observed((1,1),{(1,1),(2,1)});s['minimap']['known_cells']=[[0,0]];s['frame_sha256']='f1'
        nav=FloorNavigation()
        for _ in range(5):nav.observe(s)
        self.assertNotIn((2,1),nav.disputed())
        s['frame_sha256']='f2';nav.observe(s);self.assertIn((2,1),nav.disputed())
        nav.failures[(1,1),'d']=2
        s['frame_sha256']='f3';s['minimap']['known_cells'].append([1,0]);nav.observe(s)
        self.assertIn((2,1),nav.disputed())
        s['frame_sha256']='f4';nav.observe(s);self.assertNotIn((2,1),nav.disputed())
        self.assertEqual(nav.failures[(1,1),'d'],0)
