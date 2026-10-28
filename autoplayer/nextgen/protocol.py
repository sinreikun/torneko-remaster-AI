"""Small wire IDs and a typed grammar. Unknown inputs are never repaired by guessing."""
import copy


def wire_protocol(schema,candidates,state):
    schema=copy.deepcopy(schema)
    nonempty={'type':'string','minLength':1,'maxLength':500}
    for name in ('objective','reason'):schema['properties'][name]=copy.deepcopy(nonempty)
    for section,field in (('candidate_values','reason'),('hypotheses','explanation'),('interpretations','description')):
        schema['properties'][section]['items']['properties'][field]=copy.deepcopy(nonempty)
    schema['properties']['interpretations']['items']['properties']['quote']={'type':'string','maxLength':240}
    representatives=[];seen=set()
    for candidate in sorted(candidates,key=lambda c:c['hold_ms']):
        if tuple(candidate['keys']) not in seen:
            seen.add(tuple(candidate['keys']));representatives.append(candidate)
    aliases={f'A{i:02d}':c['id'] for i,c in enumerate(representatives)}
    wire=[{**c,'id':alias} for alias,c in zip(aliases,representatives)]
    ids=list(aliases)
    schema['properties']['candidate_values']['items']['properties']['id']={'type':'string','enum':ids}
    schema['properties']['hypotheses']['items']['properties']['candidate']={'type':'string','enum':ids}
    metric_features=[f'metric.{key}.{i}' for key,m in state['metrics'].items() for i in range(len(m['values']))]
    effect_features=metric_features+['dx','dy','interface_changed','text_changed','state_changed','floor_changed']
    schema['properties']['hypotheses']['items']['properties']['feature']={'type':'string','enum':effect_features}
    common={'type':'object','additionalProperties':False,'properties':{'weight':{'type':'number','minimum':0,'maximum':2}},'required':['feature','operator','value','weight']}
    variants=[]
    for features,operators,value in [(['text'],['eq','contains'],{'type':'string','minLength':1,'maxLength':500}),(['node'],['eq'],{'type':'string','enum':list(dict.fromkeys([state['id']]+state.get('known_nodes',[])))}), (metric_features,['eq','ge','le'],{'type':'number'})]:
        if not features:continue
        item=copy.deepcopy(common);item['properties'].update(feature={'type':'string','enum':features},operator={'type':'string','enum':operators},value=value);variants.append(item)
    schema['properties']['targets']['items']={'anyOf':variants}
    objects=[o['id'] for o in state['objects']]
    if objects:schema['properties']['interpretations']['items']['properties']['source_id']={'type':'string','enum':objects}
    else:schema['properties']['interpretations']['maxItems']=0
    return wire,aliases,schema


def decode_parts(value,aliases,candidates,state,validate):
    """Keep independently valid proposals. Bad headers still reject the reply."""
    if not isinstance(value,dict):raise ValueError('監督応答がオブジェクトではありません')
    value=copy.deepcopy(value);keys=('targets','candidate_values','hypotheses','interpretations')
    baseline={**value,**{k:[] for k in keys}}
    # Require the original keys, list types and bounded lengths as well.
    if set(value)!=set(baseline) or any(k not in value or not isinstance(value[k],list) or len(value[k])>(8 if k=='candidate_values' else 4) for k in keys):raise ValueError('監督応答の項目・配列が不正')
    accepted=validate(baseline,candidates,state);issues=[]
    for key in keys:
        for index,original in enumerate(value[key]):
            item=copy.deepcopy(original)
            try:
                if key in ('candidate_values','hypotheses'):
                    field='id' if key=='candidate_values' else 'candidate'
                    if not isinstance(item,dict) or item.get(field) not in aliases:raise ValueError('列挙されていない候補ID')
                    item[field]=aliases[item[field]]
                trial={**baseline,key:[item]};validate(trial,candidates,state)
                accepted[key].append(item)
            except (ValueError,TypeError,KeyError) as error:issues.append({'section':key,'index':index,'reason':str(error)[:200]})
    return accepted,issues
