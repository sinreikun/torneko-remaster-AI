"""Small semantic supervision: choose priorities and an observable local test."""
import copy,re
from .protocol import decode_parts

TESTS=['none','interface_closed','interface_opened','text_appears','text_disappears','selection_matches','state_changes','metric_increases','metric_decreases']

def schema_for(aliases):
    text={'type':'string','maxLength':240}
    return {'type':'object','additionalProperties':False,'properties':{
      'local_objective':{**text,'minLength':1,'description':'今の画面から数手で達成する具体的な目的。長期目標の転記ではない'},
      'reason':{**text,'minLength':1},'preferred':{'type':'array','maxItems':3,'items':{'type':'string','enum':list(aliases)}},
      'discouraged':{'type':'array','maxItems':3,'items':{'type':'string','enum':list(aliases)}},
      'success_test':{'type':'string','enum':TESTS},'success_text':text,
      'review_after':{'type':'integer','minimum':8,'maximum':128}},
      'required':['local_objective','reason','preferred','discouraged','success_test','success_text','review_after']}

def decode(value,aliases,candidates,state,validate):
    # Archived protocol replies remain readable, but live requests use only the
    # smaller schema. No numeric score/hold-time coercion or guessed key mapping.
    if isinstance(value,dict) and 'local_objective' not in value:return decode_parts(value,aliases,candidates,state,validate)
    if not isinstance(value,dict) or set(value)!=set(schema_for(aliases)['required']):raise ValueError('短期監督応答の項目が不正')
    for field in ('local_objective','reason'):
        if not isinstance(value[field],str) or not 1<=len(value[field])<=240:raise ValueError('短期目的と理由が必要')
    if type(value['review_after']) is not int or not 8<=value['review_after']<=128:raise ValueError('見直し間隔が不正')
    if value['success_test'] not in TESTS or not isinstance(value['success_text'],str) or len(value['success_text'])>240:raise ValueError('短期の確認条件が不正')
    for key in ('preferred','discouraged'):
        ids=value[key]
        if not isinstance(ids,list) or len(ids)>3 or any(not isinstance(v,str) or v not in aliases for v in ids) or len(set(ids))!=len(ids):raise ValueError('未許可または重複の優先候補')
    if set(value['preferred'])&set(value['discouraged']):raise ValueError('優先と抑制の候補が矛盾')
    plan={'objective':value['local_objective'],'reason':value['reason'],'review_after':value['review_after'],'targets':[],
      'candidate_values':[{'id':aliases[a],'value':1 if key=='preferred' else -1,'reason':value['reason']} for key in ('preferred','discouraged') for a in value[key]],'hypotheses':[],'interpretations':[]}
    test=value['success_test'];text=value['success_text']
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
    # Values above are constructed from enums, never model-supplied program code.
    return plan,[]

def unavailable_key_hints(quotes,allowed):
    names={'tab':'tab','esc':'escape','escape':'escape','enter':'enter','space':'space'}
    out=[]
    for quote in quotes:
        for match in re.finditer(r'(?<![A-Za-z])(Tab|Escape|Esc|Enter|Space)(?![A-Za-z])',quote,re.I):
            key=names[match[1].lower()]
            if key not in allowed and not any(x['key']==key for x in out):out.append({'key':key,'quote':quote,'status':'screen_key_hint_not_authorized'})
    return out
