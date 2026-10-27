"""Map decisions depend on geometry/direction outcomes, never visit counters."""
import copy,json,unittest
from pathlib import Path
from autoplayer.floor_navigation import FloorNavigation
from autoplayer.telemetry import RecognitionState
from test_floor_navigation import observed
from test_policy import receipt_row

class VisitIndependenceTests(unittest.TestCase):
    def test_goal_path_frontiers_and_completed_branches_ignore_visit_counts(self):
        known={(x,3) for x in range(1,9)}|{(4,2),(4,1)}
        s=observed((3,3),known)
        baseline=None
        for visits in (0,1,10000):
            nav=FloorNavigation();nav.observe(s)
            for c in known:nav.visited[c]=visits
            for key in ('w','a','s'):nav.failures[(1,3),key]=1
            hint=nav.hint(s)
            result=(nav.primary(),nav.boundaries(),nav.retired(),nav.disputed(),hint)
            if baseline is None:baseline=result
            else:self.assertEqual(result,baseline)
    def test_checking_directions_completes_endpoint_without_any_visit_history(self):
        known={(1,1),(2,1),(3,1)};s=observed((2,1),known);nav=FloorNavigation();nav.observe(s)
        nav.visited.clear()
        self.assertIn((3,1),nav.primary())
        for key in ('w','d','s'):nav.failures[(3,1),key]=1
        self.assertNotIn((3,1),nav.primary());self.assertIn((3,1),nav.retired())
        self.assertNotIn((2,1),nav.retired()) # Its own outward directions are still unresolved.
        self.assertIsNotNone(nav._path({(3,1)}))
    def test_position_counter_does_not_override_current_conflicting_map_evidence(self):
        s=observed((1,1),{(1,1),(2,1),(3,1)});nav=FloorNavigation();nav.observe(s)
        nav.missing_floor[(3,1)]=2;nav.visited[(3,1)]=1000
        self.assertIn((3,1),nav.disputed())
        nav.crossings[nav.edge_pair((2,1),(3,1))]=1
        self.assertNotIn((3,1),nav.disputed()) # Actual input-linked passage is separate evidence.
    def test_first_connection_is_measured_even_when_destination_has_high_visit_count(self):
        a=observed((1,1),{(1,1),(2,1)});b=observed((2,1),{(1,1),(2,1)})
        nav=FloorNavigation();nav.observe(a);nav.visited[(2,1)]=999
        row=receipt_row('d');nav.record(a,b,row);self.assertTrue(row['floor_navigation_outcome']['new_connection'])
        nav.visited.clear();reverse=receipt_row('a');nav.record(b,a,reverse)
        self.assertFalse(reverse['floor_navigation_outcome']['new_connection'])
    def test_monitor_does_not_color_known_floor_by_visit_count(self):
        s=observed((2,1),{(1,1),(2,1),(3,1)});s['floor_exploration']={'status':'observed_floor'}
        fresh=RecognitionState();fresh.update({'event':'perception','policy_state':s})
        s['floor_exploration'].update(visited_count=99,visited_cells=[[1,1],[2,1],[3,1]])
        old=RecognitionState();old.update({'event':'perception','policy_state':s})
        self.assertEqual(fresh.grid(),old.grid());self.assertNotIn('踏破',old.summary())
    def test_llm_summary_excludes_visit_counts(self):
        from autoplayer.performance import bounded_request_prompt
        prompt={'task':'operational_supervision','goal':'探索','state_ledger':{'current':{'floor_exploration':{'known_count':3,'visited_count':999,'visited_cells':[[2,1]],'primary_frontier_count':2}}}}
        summary=bounded_request_prompt(prompt)
        self.assertEqual(summary['state_ledger']['current']['floor_counts'],{'known_count':3,'primary_frontier_count':2})
    def test_mapless_known_floor_still_allows_checking_an_unknown_connection(self):
        from autoplayer.core import Settings
        from autoplayer.mapless_navigation import MaplessNavigation
        results=[]
        for visits in (0,10000):
            nav=MaplessNavigation(Settings(window_title='トルネコ'));nav.active=True
            nav.nodes={p:{'visits':visits} for p in ((0,0),(0,-1),(1,0),(0,1),(-1,0))}
            results.append(nav.propose())
        self.assertEqual(results[0],results[1]);self.assertEqual(results[0][0],['w'])
