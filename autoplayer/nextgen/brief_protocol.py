"""Small semantic supervision: choose priorities and an observable local test."""
import copy,re
from .protocol import decode_parts

TESTS=['none','interface_closed','interface_opened','text_appears','text_disappears','selection_matches','state_changes','metric_increases','metric_decreases']

def success_options(state):
    """Finite, grounded local tests; labels are protocol, never screen text."""
    options=[{'id':'S00','test':'none','text':''}]
    visible=state.get('interface_observation',{}).get('visible')
    if type(visible) is bool:options.append({'id':'S01','test':'interface_closed' if visible else 'interface_opened','text':''})
    options.append({'id':'S02','test':'state_changes','text':''})
    ui=state.get('ui_visual',{});seen=set()
    selected=ui.get('selection',{});selection_ids={selected['element_id']} if selected.get('status')=='supported_association' and selected.get('element_id') else set()
    for edge in ui.get('graph',[]):
        if edge.get('kind')=='selection_movement_hypothesis' and edge.get('support',0)>=2 and edge.get('confidence',0)>=.6 and not edge.get('counterexamples') and edge.get('status')!='under_review':
            selection_ids.update([edge['origin'],edge['destination']])
    panels=state.get('interface_observation',{}).get('rectangles',[])
    for e in ui.get('elements',[]):
        text=e.get('text');box=e.get('bbox',[])
        if not isinstance(text,str) or not text.strip() or len(text)>60 or text in seen or len(box)!=4 or box[3]-box[1]>.12:continue
        if text not in '\n'.join(state.get('quotes',[])):continue
        x=(box[0]+box[2])/2;y=(box[1]+box[3])/2
        if panels and not any(r[0]<=x<=r[2] and r[1]<=y<=r[3] for r in panels):continue
        seen.add(text)
        for test in (('selection_matches','text_disappears') if e.get('id') in selection_ids else ('text_disappears',)):
            options.append({'id':'S'+str(len(options)+1).zfill(2),'test':test,'text':text})
        if len(seen)>=4:break
    for name,metric in list(state.get('metrics',{}).items())[:2]:
        for index,value in enumerate(metric.get('values',[])[:1]):
            if type(value) not in (int,float):continue
            for test in ('metric_increases','metric_decreases'):
                options.append({'id':'S'+str(len(options)+1).zfill(2),'test':test,'text':f'metric.{name}.{index}'})
    return options


def schema_for(aliases,planning=False,new_goal_ids=None,existing_goal_ids=(),success_choices=None):
    text={'type':'string','maxLength':240}
    schema={'type':'object','additionalProperties':False,'properties':{
      'local_objective':{**text,'minLength':1,'description':'今の画面から数手で達成する具体的な目的。長期目標の転記ではない'},
      'reason':{**text,'minLength':1},'preferred':{'type':'array','maxItems':3,'items':{'type':'string','enum':list(aliases)}},
      'discouraged':{'type':'array','maxItems':3,'items':{'type':'string','enum':list(aliases)}},
      'success_test':{'type':'string','enum':TESTS},'success_text':text,
      'review_after':{'type':'integer','minimum':8,'maximum':128}},
      'required':['local_objective','reason','preferred','discouraged','success_test','success_text','review_after']}
    if success_choices is not None:
        for key in ('success_test','success_text'):
            schema['properties'].pop(key);schema['required'].remove(key)
        schema['properties']['success_choice']={'type':'string','enum':[x['id'] for x in success_choices],'description':'提示された成功条件候補IDを一つ選ぶ。不明ならS00。'}
        schema['required'].append('success_choice')
    from .knowledge_access import TOPICS
    schema['properties']['information_requests']={'type':'array','maxItems':2,'items':{'type':'object','additionalProperties':False,'properties':{'topic':{'type':'string','enum':sorted(TOPICS)},'target_id':{'type':'string','maxLength':100}},'required':['topic','target_id']}}
    schema['properties']['observation_request']={'type':'string','enum':['none','fresh_full_ocr']}
    from .progress import CAPABILITIES
    schema['properties']['required_capability']={'type':'string','enum':list(CAPABILITIES)}
    if planning:
        condition={'type':'object','additionalProperties':False,'properties':{
          'feature':{'type':'string','maxLength':100},'operator':{'type':'string','enum':['eq','ne','contains','not_contains','ge','le']},
          'value':{'type':['string','number','boolean']}},'required':['feature','operator','value']}
        node={'type':'object','additionalProperties':False,'properties':{
          'id':{'type':'string','minLength':1,'maxLength':80},'label':{**text,'minLength':1},'parent':text,
          'requires':{'type':'array','maxItems':8,'items':{'type':'string'}},
          'scope':{'type':'string','enum':['campaign','session']},
          'conditions':{'type':'array','minItems':1,'maxItems':4,'items':condition},
          'counterconditions':{'type':'array','maxItems':4,'items':condition},
          'capabilities':{'type':'array','maxItems':4,'items':{'type':'string','enum':list(CAPABILITIES)}},'source':{**text,'minLength':1}},
          'required':['id','label','parent','requires','scope','conditions','counterconditions','capabilities','source']}
        if new_goal_ids:
            node['properties']['id']['enum']=list(new_goal_ids)
            node['properties']['parent']['enum']=['']+list(new_goal_ids)+list(existing_goal_ids)
            node['properties']['requires']['items']['enum']=list(new_goal_ids)+list(existing_goal_ids)
        schema['properties']['session_boundary']={'type':'array','maxItems':4,'items':condition,'description':'冒険の終了/再開始を示す現在観測可能な条件。複数フレームで検証する提案。不要なら空配列。'}
        schema['properties']['progress_nodes']={'type':'array','minItems':1,'maxItems':3,'items':node,'description':'計画案のみ。資料は証拠でない。featureはtext/selection_text/interface_open/map_observed/metric.NAME.INDEX。既存IDを上書きしない。'}
        schema['required'].append('progress_nodes')
    return schema


def decode(value,aliases,candidates,state,validate):
    if isinstance(value,dict) and 'success_choice' in value:
        if 'success_test' in value or 'success_text' in value:raise ValueError('Mixed success protocols')
        choices=state.get('success_choices',[])
        choice=next((x for x in choices if x['id']==value['success_choice']),None)
        if not choice:raise ValueError('Unknown grounded success choice')
        value={k:v for k,v in value.items() if k!='success_choice'}
        value.update(success_test=choice['test'],success_text=choice['text'])
    # Archived protocol replies remain readable, but live requests use only the
    # smaller schema. No numeric score/hold-time coercion or guessed key mapping.
    if isinstance(value,dict) and 'local_objective' not in value:return decode_parts(value,aliases,candidates,state,validate)
    if not isinstance(value,dict) or not set(schema_for(aliases)['required'])<=set(value) or set(value)-set(schema_for(aliases)['required'])-{'required_capability','progress_nodes','session_boundary','information_requests','observation_request'}:raise ValueError('短期監督応答の項目が不正')
    for field in ('local_objective','reason'):
        if not isinstance(value[field],str) or not 1<=len(value[field])<=240:raise ValueError('短期目的と理由が必要')
    if type(value['review_after']) is not int or not 8<=value['review_after']<=128:raise ValueError('見直し間隔が不正')
    if value['success_test'] not in TESTS or not isinstance(value['success_text'],str) or len(value['success_text'])>240:raise ValueError('短期の確認条件が不正')
    for key in ('preferred','discouraged'):
        ids=value[key]
        if not isinstance(ids,list) or len(ids)>3 or any(not isinstance(v,str) or v not in aliases for v in ids) or len(set(ids))!=len(ids):raise ValueError('未許可または重複の優先候補')
    if set(value['preferred'])&set(value['discouraged']):raise ValueError('優先と抑制の候補が矛盾')
    from .knowledge_access import validate_requests
    requests=validate_requests(value.get('information_requests',[]))
    observation=value.get('observation_request','none')
    if observation not in ('none','fresh_full_ocr'):raise ValueError('Invalid observation request')
    issues=[]
    plan={'objective':value['local_objective'],'reason':value['reason'],'review_after':value['review_after'],'targets':[],
      'candidate_values':[{'id':aliases[a],'value':1 if key=='preferred' else -1,'reason':value['reason']} for key in ('preferred','discouraged') for a in value[key]],'hypotheses':[],'interpretations':[]}
    plan['information_requests']=requests;plan['observation_request']=observation
    test=value['success_test'];text=value['success_text']
    if value['local_objective'] in TESTS and value['local_objective']!=test:raise ValueError('目的のプロトコル名と成功条件が矛盾')
    if test in ('text_appears','text_disappears','selection_matches'):
        if not text.strip():raise ValueError('文字条件には比較する原文が必要')
        if test=='selection_matches' and text not in '\n'.join(state['quotes']):raise ValueError('未観測の選択項目')
        plan['targets']=[{'feature':'selection_text' if test=='selection_matches' else 'text','operator':'contains' if test!='text_disappears' else 'not_contains','value':text,'weight':1}]
    elif test in ('interface_closed','interface_opened'):
        plan['targets']=[{'feature':'interface_open','operator':'eq','value':int(test=='interface_opened'),'weight':1}]
    elif test in ('metric_increases','metric_decreases'):
        from .model import value_at
        current=value_at(state,text) if text.startswith('metric.') else None
        if type(current) not in (int,float):raise ValueError('未観測の数値を増減目標には使えません')
        plan['targets']=[{'feature':text,'operator':'ge' if test=='metric_increases' else 'le','value':current+(1 if test=='metric_increases' else -1),'weight':1}]
    elif test=='state_changes':plan['targets']=[{'feature':'node','operator':'ne','value':state['id'],'weight':1}]
    if 'required_capability' in value:
        from .progress import CAPABILITIES
        if value['required_capability'] not in CAPABILITIES:raise ValueError('Unknown capability')
        plan['required_capability']=value['required_capability']
    if 'progress_nodes' in value:
        from .progress import validate_nodes
        # Existing dependencies are checked again by ProgressManager on adoption.
        known=state.get('progress_node_ids',[])
        try:plan['progress_nodes']=validate_nodes(value['progress_nodes'],known)
        except ValueError as error:issues.append('progress_proposal_invalid: '+str(error))
    if 'session_boundary' in value:
        from .progress import validate_condition
        if not isinstance(value['session_boundary'],list) or len(value['session_boundary'])>4:raise ValueError('Invalid session boundary')
        plan['session_boundary']=[validate_condition(c) for c in value['session_boundary']]
    # Values above are constructed from enums, never model-supplied program code.
    return plan,issues

def unavailable_key_hints(quotes,allowed):
    names={'tab':'tab','esc':'escape','escape':'escape','enter':'enter','space':'space'}
    out=[]
    for quote in quotes:
        for match in re.finditer(r'(?<![A-Za-z])(Tab|Escape|Esc|Enter|Space)(?![A-Za-z])',quote,re.I):
            key=names[match[1].lower()]
            if key not in allowed and not any(x['key']==key for x in out):out.append({'key':key,'quote':quote,'status':'screen_key_hint_not_authorized'})
    return out
