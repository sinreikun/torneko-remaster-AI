"""Coordinate/graph contracts only. No rendered game or live input."""
import copy
import unittest
from autoplayer.exploration import ExplorationRoutes
from test_policy import state


def point(x,y=22):
    s=state(str((x,y)),x*6)
    s['minimap']['player']=[x*6,y*6];s['minimap']['player_cell']=[x,y]
    return s


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.routes=ExplorationRoutes()
        self.candidates=[{'keys':[k]} for k in 'wasd']

    def test_routes_over_observed_edges_to_remaining_probe(self):
        a,b=point(40),point(41)
        self.routes.record(a,b,'d',{});self.routes.record(b,a,'a',{})
        for k in 'wasd':self.routes.attempts[(40,22),k]=1
        result=self.routes.suggest(a,self.candidates)
        self.assertEqual((result['key'],result['target_cell'],result['distance']),('d',[41,22],1))
        self.assertEqual(result['mode'],'observed_route')
        self.assertIsNone(self.routes.suggest(a,[v for v in self.candidates if v['keys']!=['d']]))

    def test_no_inferred_reverse_edge_and_no_failed_move_as_wall(self):
        a,b=point(40),point(41)
        self.routes.record(a,b,'d',{})
        self.assertNotIn(((41,22),'a'),self.routes.edges)
        self.routes.record(b,b,'w',{})
        self.assertEqual(self.routes.attempts[(41,22),'w'],1)
        self.assertTrue(self.routes.nodes[(41,22)]['w']['known'])
        self.assertNotIn(((41,22),'w'),self.routes.edges)

    def test_natural_motion_wrong_direction_and_jump_do_not_create_routes(self):
        a,b=point(40),point(41)
        self.routes.record(a,b,'d',{'position':1})
        self.assertFalse(self.routes.edges);self.assertFalse(self.routes.attempts)
        self.routes.record(a,b,'w',{})
        self.assertFalse(self.routes.edges)
        self.routes.record(a,b,'d',{});self.assertTrue(self.routes.edges)
        self.routes.record(b,point(50),'d',{})
        self.assertFalse(self.routes.nodes);self.assertFalse(self.routes.edges)

    def test_floor_and_scale_changes_reset_routes(self):
        a,b=point(40),point(41)
        for altered in ('floor','scale'):
            self.routes.record(a,b,'d',{})
            changed=copy.deepcopy(b)
            if altered=='floor':changed['metrics']['floor']['values']=[2]
            else:changed['minimap']['cell_size']=3
            self.routes.node(changed)
            self.assertFalse(self.routes.edges);self.assertFalse(self.routes.attempts)

    def test_large_map_loss_resets_but_marker_shape_change_keeps_visits(self):
        a,b=point(40),point(41)
        self.routes.record(a,b,'d',{})
        changed=copy.deepcopy(b);changed['minimap']['shape']='new-marker-shape'
        self.assertEqual(self.routes.visits(changed),1)
        changed['minimap']['known_pixels']=100
        self.assertEqual(self.routes.visits(changed),0)
        self.assertFalse(self.routes.edges)

    def test_enemy_and_unknown_neighbor_are_not_known_frontiers(self):
        a=point(40)
        for k in 'wasd':a['minimap']['neighbors'][k]['known']=False
        a['minimap']['neighbors']['d']['known']=True
        self.assertEqual(self.routes.suggest(a,self.candidates)['key'],'d')
        a['minimap']['neighbors']['d']['enemy']=True
        self.assertNotEqual(self.routes.suggest(a,self.candidates)['key'],'d')
