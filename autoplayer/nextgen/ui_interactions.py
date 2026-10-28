"""Learn whether an input advances text, selects, or has no measured UI effect.
Structural families share evidence across changing sentences, never across profiles.
"""
import copy,re,unicodedata,uuid
from collections import OrderedDict
from .state_hash import digest


def text_signature(state):
    quotes=state.get('quotes',[])
    return digest(sorted({re.sub(r'\s+','',unicodedata.normalize('NFKC',q)) for q in quotes if isinstance(q,str) and q.strip()}))

def family_of(state):
    ui=state.get('ui_visual',{});rects=state.get('interface_observation',{}).get('rectangles',[])
    if not (state.get('interface_hypothesis') or ui.get('active')):return None
    cue=any(c.get('status')=='symbol_hypothesis' for c in ui.get('candidates',[]))
    if not rects:return digest(['ui-layout-fallback-v1',ui['layout'],cue]) if ui.get('layout') else None
    boxes=sorted([[round(v*40)/40 for v in b] for b in rects[:8]])
    grid=ui.get('grid',{});grid_shape=[grid.get('rows'),grid.get('columns')] if grid.get('status')=='hypothesis' else None
    return digest(['ui-structure-v1',boxes,grid_shape,cue])

class UIInteractionModel:
    def __init__(self):self.models=OrderedDict();self.session=uuid.uuid4().hex[:12]
    def restore(self,models):
        for model in models:
            if model.get('schema')==1:self.models[model['key']]=copy.deepcopy(model)
    def model(self,state):
        key=family_of(state)
        if not key:return None
        m=self.models.setdefault(key,{'key':key,'schema':1,'actions':{},'seen_text':[],'recent':[],'checks':[]})
        self.models.move_to_end(key)
        while len(self.models)>64:self.models.popitem(last=False)
        return m
    def attach(self,state):
        ui=state.get('ui_visual')
        if not ui:return state
        m=self.model(state)
        if not m:
            ui['interaction_model']={'family':None,'selection_requirement':'unknown','actions':[],'cycle_detected':False};return state
        actions=[]
        for action in m['actions'].values():
            successes=action['text_responses'];failures=action['no_responses'];confidence=(successes+1)/(successes+failures+2)
            actions.append({'id':action['id'],'keys':action['keys'],'trials':action['trials'],'text_responses':successes,'no_responses':failures,
                            'selection_responses':action['selection_responses'],'confidence':confidence,
                            'controlled_responses':action.get('controlled_responses',0),'status':'supported_text_flow_association' if successes>=3 and confidence>=.75 and len(action['distinct_text'])>=3 and action.get('controlled_responses',0)>=1 else 'hypothesis'})
        selection_history=any(a['selection_responses'] for a in actions)
        selectable=any(c.get('status')=='symbol_hypothesis' for c in ui.get('candidates',[])) or ui.get('grid',{}).get('status')=='hypothesis' or ui.get('selection',{}).get('status')=='supported_association' or selection_history
        flow=any(a['status']=='supported_text_flow_association' for a in actions)
        recent=m['recent'];cycle=len(recent)>=12 and not any(e.get('new_text') for e in recent[-6:]) and len({e['destination'] for e in recent[-12:]})<=6
        sequence=[e['destination'] for e in recent]
        cycle=cycle or any(len(sequence)>=2*period and sequence[-period:]==sequence[-2*period:-period] and not any(e.get('new_text') for e in recent[-period:]) for period in range(2,9))
        requirement='selection_capable' if selectable else 'not_required_for_supported_text_flow' if flow else 'unknown'
        ui['interaction_model']={'family':m['key'],'selection_requirement':requirement,'actions':actions[:32],'cycle_detected':cycle,
                                 'basis':'observed_input_response_association_not_ui_semantic_truth'}
        ui['needs_observation']=[x for x in ui.get('needs_observation',[]) if x!='selection_text_correspondence_unknown']
        if requirement!='not_required_for_supported_text_flow' and ui.get('selection',{}).get('status')!='supported_association':ui['needs_observation'].append('selection_text_correspondence_unknown')
        if requirement=='not_required_for_supported_text_flow':ui['coverage']=max(ui.get('coverage',0),1. if ui.get('elements') else .5)
        return state
    def observe(self,before,action,after,result,receipt,step,transition,neutral=None):
        m=self.model(before);key=family_of(after)
        if not m or result.get('status')!='updated' or receipt.get('os_submission')!='accepted':return {'status':'censored_or_unscoped'}
        identifier=digest([self.session,step,before['frame_sha256'],after['frame_sha256'],action['id']])
        if identifier in m['checks']:return {'status':'duplicate'}
        m['checks'].append(identifier);m['checks']=m['checks'][-64:]
        a=m['actions'].setdefault(action['id'],{'id':action['id'],'keys':list(action['keys']),'trials':0,'text_responses':0,'no_responses':0,'selection_responses':0,'controlled_responses':0,'confounded':0,'distinct_text':[],'evidence':[]})
        a['trials']+=1
        text_available=before.get('measurement_status')==after.get('measurement_status')=='ok' and bool(before.get('quotes')) and bool(after.get('quotes'))
        previous=text_signature(before);current=text_signature(after)
        natural=bool(neutral and neutral.get('ui_text_content_changed',neutral.get('text_changed')))
        changed=bool(text_available and previous!=current)
        region_changed=transition.get('panel_pixel_change',0)>1.
        selected_before=before.get('selection_observation',{});selected_after=after.get('selection_observation',{})
        selection_change=selected_before.get('status')==selected_after.get('status')=='supported_association' and selected_before.get('element_id')!=selected_after.get('element_id')
        flow=key==m['key'] and changed and region_changed and not natural and not selection_change
        no_effect=key==m['key'] and text_available and not changed and transition.get('status')=='no_measured_ui_change' and not natural
        if flow:
            a['text_responses']+=1
            if neutral is not None and neutral.get('ui_text_content_changed',neutral.get('text_changed'))==0:a['controlled_responses']=a.get('controlled_responses',0)+1
            if current not in a['distinct_text']:a['distinct_text'].append(current);a['distinct_text']=a['distinct_text'][-16:]
        if no_effect:a['no_responses']+=1
        if selection_change:a['selection_responses']+=1
        if natural:a['confounded']+=1
        new_text=flow and current not in m['seen_text']
        if previous not in m['seen_text']:m['seen_text'].append(previous)
        if current not in m['seen_text']:m['seen_text'].append(current)
        m['seen_text']=m['seen_text'][-128:]
        if flow:m['recent'].append({'origin':previous,'destination':current,'action':action['id'],'new_text':new_text,'step':step});m['recent']=m['recent'][-24:]
        evidence={'step':step,'before_frame':before['frame_sha256'],'after_frame':after['frame_sha256'],'text_response':flow,'no_response':no_effect,'selection_response':selection_change,'natural_confound':natural,'new_text':new_text,'same_structure':key==m['key']}
        a['evidence'].append(evidence);a['evidence']=a['evidence'][-8:]
        after.setdefault('ui_visual',{})['interaction_effect']=evidence
        self.attach(after)
        return evidence
    @staticmethod
    def priors(state,candidates,explore=False):
        ui=state.get('ui_visual',{});model=ui.get('interaction_model',{});actions={a['id']:a for a in model.get('actions',[])}
        nongrid=ui.get('grid',{}).get('status')!='hypothesis';values=[]
        for candidate in candidates:
            a=actions.get(candidate['id'],{});score=None;reason=''
            if a.get('text_responses',0)>=2 and a.get('confidence',0)>=.65 and nongrid and not (model.get('selection_requirement')=='selection_capable' and ui.get('selection',{}).get('status')!='supported_association'):
                score=1.5 if a.get('status')=='supported_text_flow_association' else 1.
                reason='同じUI構造でこの入力に文章領域の反応を繰り返し観測した。選択位置の存在は仮定しない。'
                if model.get('cycle_detected'):score=-2.;reason='既読の文章状態を循環している入力。別の操作・目標へ切り替える。'
            elif a.get('no_responses',0)>=3 and not a.get('text_responses') and not a.get('selection_responses'):
                score=-1.5;reason='このUI構造では入力後の可視効果が複数回未確認。OS入力異常とは扱わない。'
            elif explore and nongrid and model.get('selection_requirement')!='selection_capable' and a.get('trials',0)<2 and len(candidate['keys'])==1:
                meanings=' '.join(candidate.get('declared_meanings',[]))
                if re.search(r'決定|confirm|select',meanings,re.I):score=.9;reason='選択が必要か不明なUIで、利用者指定の決定入力の反応を有限回検証する。'
            if model.get('cycle_detected') and a.get('trials',0)<2 and len(candidate['keys'])==1 and re.search(r'キャンセル|取消|戻る|cancel|back',' '.join(candidate.get('declared_meanings',[])),re.I):
                score=1.;reason='文章の循環から離れる入力の効果を有限回検証する。'
            if score is not None:values.append({'id':candidate['id'],'value':score,'reason':reason})
        return values
