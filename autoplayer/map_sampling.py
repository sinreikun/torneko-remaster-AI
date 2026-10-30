"""Throttle map-only display reads. Input-based positions are never observations."""
import copy

class MapSamplingPlan:
    def __init__(self,interval=4,live_objects=False):self.live_objects=live_objects;self.interval=interval;self.last_step=None;self.floor=None;self.position=None;self.prediction=None;self.hp=None;self.invalid=False;self.inputs=[];self.anchor=None;self.force_checks=0
    def due(self,state,step,phase):
        metrics=state.get('metrics',{});floor=metrics.get('floor',{}).get('values');hp=metrics.get('hp',{}).get('values')
        if self.live_objects and floor and not state.get('menu_evidence') and phase in ('map_observation','map_result'):return True
        if self.last_step is None or floor!=self.floor or self.invalid:return True
        if hp and self.hp and hp[0]<self.hp[0]:return True
        return phase!='map_result' and (self.force_checks>0 or step-self.last_step>=self.interval)
    def confirmed(self,state,step):
        if state.get('minimap',{}).get('status')!='observed':return
        actual=state['minimap'].get('player_cell');predicted=copy.deepcopy(self.position)
        comparison={'inputs':copy.deepcopy(self.inputs),'previous_observed':copy.deepcopy(self.anchor),'observed':copy.deepcopy(actual),'predicted_unverified':predicted,'attribution':'unresolved_sequence_not_single_action_cause'} if self.inputs else None
        if self.inputs and predicted and actual and predicted!=actual:self.force_checks=2
        elif self.force_checks:self.force_checks-=1
        self.inputs=[];self.anchor=copy.deepcopy(actual)
        self.last_step=step;self.floor=copy.deepcopy(state['metrics'].get('floor',{}).get('values'));self.position=copy.deepcopy(state['minimap'].get('player_cell'))
        self.hp=copy.deepcopy(state['metrics'].get('hp',{}).get('values'));self.invalid=False
        self.prediction={'status':'map_observed','player_cell':self.position,'source_frame_sha256':state.get('frame_sha256'),'reconciliation':comparison}
    def after_input(self,keys,state):
        self.inputs=(self.inputs+[list(keys)])[-16:]
        floor=state.get('metrics',{}).get('floor',{}).get('values')
        if floor!=self.floor:self.invalid=True;self.position=None
        moves={'w':(0,-1),'a':(-1,0),'s':(0,1),'d':(1,0)}
        if self.position and len(keys)==1 and keys[0] in moves and not state.get('menu_evidence'):
            d=moves[keys[0]];p=[self.position[i]+d[i] for i in (0,1)]
            if not (0<=p[0]<54 and 0<=p[1]<32):self.invalid=True;self.position=None
            else:self.position=p
        elif keys:self.position=None
        self.prediction={'status':'input_based_unverified','estimated_player_cell':copy.deepcopy(self.position),'keys':list(keys),
            'note':'送信キーからの仮位置。壁・無反応・複数マス移動を含み得る。地図の観測値や経路学習に使わない。'}
    def context(self):return copy.deepcopy(self.prediction or {'status':'unavailable'})
