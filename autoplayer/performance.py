"""Bound inference payloads; retain full images/OCR in local evidence."""
import io


def vision_image(png):
    if png is None or not png.startswith(b'\x89PNG\r\n\x1a\n'):return png
    from PIL import Image
    try:
        with Image.open(io.BytesIO(png)) as source:
            if max(source.size)<=768:return png
            image=source.convert('RGB');image.thumbnail((768,768),Image.Resampling.LANCZOS)
            out=io.BytesIO();image.save(out,format='PNG');return out.getvalue()
    except (OSError,ValueError):return png


def ocr_context(native):
    return {**{k:native[k] for k in ('status','engine','frame_sha256','language') if k in native},
        'lines':[{'text':line.get('text','')[:120]} for line in native.get('lines',[])[:20]],
        'note':'OCR原文の抜粋。座標と全画面/上部の重複読取はローカル記録に保持。'}


def knowledge_context(value):
    if not value:return None
    selected=value['facts'][:6];ids={s for f in selected for s in f.get('sources',[])}
    goal=value.get('active_goal')
    return {k:value[k] for k in ('catalog_sha256','edition','metrics','goal_review') if k in value}|{
        'active_goal':{**{k:v for k,v in goal.items() if k in ('objective','success_condition','reason')},'status':'model_proposal_unverified'} if isinstance(goal,dict) else goal,
        'ocr':ocr_context(value.get('ocr',{})),
        'facts':[{k:f[k] for k in ('id','summary','editions','sources','applicability','verification') if k in f} for f in selected],
        'sources':[{k:s[k] for k in ('id','url') if k in s} for s in value['sources'] if s['id'] in ids],
        'rules':'出典とOCRは未検証の参考。版違いは仮説。用語の一致で効果や達成を確定しない。全体目標を変更しない。'}


def fast_policy_ready(state,stable,anchor,floor,objective,count,blocked,stagnant,reassess):
    return bool(anchor and count<3 and stable and state and state['eligible']
        and state['metrics'].get('floor',{}).get('values')==floor and state['objective']==objective
        and state['objective']!='survive' and not state['minimap'].get('enemies')
        and not blocked and stagnant<2 and not reassess)


def compact_profile_prompt(prompt):
    """Keep optional retrieved context bounded; preserve current evidence and controls."""
    result=dict(prompt)
    result['recent_history']=result.get('recent_history',[])[-2:]
    memory=dict(result.get('memory',{}))
    for key,limit in (('provisional_claims',3),('known_transitions',2),('plan',3),('reported_progress',3)):
        if key in memory:memory[key]=memory[key][:limit]
    result['memory']=memory
    result['retrieved_experiences']=[{**row,'ocr_quotes':[q[:100] for q in row.get('ocr_quotes',[])[:5]]}
                                    for row in result.get('retrieved_experiences',[])[:1]]
    references=dict(result.get('public_references',{}))
    references['sources']=[{**source,'excerpt':source.get('excerpt','')[:350]} for source in references.get('sources',[])[:2]]
    result['public_references']=references
    if result.get('method_selection'):
        result['method_selection']={k:v for k,v in result['method_selection'].items()
                                    if k in ('method','genre_guidance','instruction','last_outcome')}
    return result


def context_retry_prompt(prompt):
    """Drop optional historical context, preserving current evidence and controls."""
    if prompt.get('task')=='operational_supervision':
        from .supervision_context import compact_supervision_prompt
        return compact_supervision_prompt(prompt,budget=5000,tight=True)
    result=dict(prompt)
    if result.get('human_examples'):
        result['human_examples']=[{**result['human_examples'][0],'steps':result['human_examples'][0].get('steps',[])[:2]}]
    for key in ('retrieved_experiences','public_references','temporal_evidence','learned_policy','self_improvement'):
        result.pop(key,None)
    result['recent_history']=[{k:v for k,v in row.items() if k in ('keys','observed_keys','executed','action_kind','changed')}
        for row in result.get('recent_history',[])[-1:]]
    memory=result.get('memory',{})
    result['memory']={k:memory[k] for k in ('current_interface','neutral_observation','relative_contacts') if k in memory}
    knowledge=result.get('source_knowledge')
    if isinstance(knowledge,dict):
        result['source_knowledge']={k:v for k,v in knowledge.items() if k in ('metrics','ocr','active_goal','goal_review','rules','edition')}
    return result


# Keep obsolete display-related prose out of planning context.
import re
REFERENCES=re.compile(r'ミニマップ|地図|マップ|minimap|\bmap\b|青い(?:物体|図形|表示|箱|四角|領域|ブロック)|黄色.{0,12}(?:四角|円形|マーカー)|赤い点|blue[_ ](?:object|box|shape|display)|yellow[_ ](?:square|circle)|red[_ ]dot',re.I)
PRIVATE_KEYS={'visited_cells','visited_count','visits','arrival_visits_before','location_first_observed','minimap','minimap_pixels','minimap_observation','player_cell','cell','map_status','_floor_observed'}

def clean_text(text):
    if text=='num7':return ''
    return '。'.join(part for part in re.split(r'[。\n]',text) if part.strip() and not REFERENCES.search(part))

def model_data(value):
    if isinstance(value,str):return clean_text(value)
    if isinstance(value,list):
        return [v for item in value if (v:=model_data(item)) not in ('',None,{},[])]
    if isinstance(value,dict):
        if 'summary' in value and REFERENCES.search(str(value.get('id',''))+' '+str(value['summary'])):return {}
        result={}
        for key,item in value.items():
            if key in PRIVATE_KEYS or key=='num7' or key.startswith('_'):continue
            cleaned=model_data(item)
            if cleaned not in ('',{},[]):result[key]=cleaned
        return result
    return value


def fallback_schema(schema):
    """Avoid duplicating the entire decision grammar inside a JSON-only prompt.

    Dependent status/key constraints remain enforced by parse_decision.
    """
    import copy
    if not isinstance(schema,dict) or 'oneOf' not in schema:return schema
    branches=schema['oneOf']
    if not branches or any('properties' not in branch for branch in branches):return schema
    result=copy.deepcopy(branches[0])
    properties=result['properties']
    properties['status']['enum']=list(dict.fromkeys(s for branch in branches for s in branch['properties']['status']['enum']))
    # Generic JSON has no dependent grammar. Allow verified completion's empty
    # action; the controller supplies an input for every continuing response.
    properties['action']['properties']['keys'].pop('minItems',None)
    required=set(result.get('required',[]))
    # Optional long-term hypotheses and forecasts are not needed for a single
    # repair. Retain intent, goal success, timing and all required protocol fields.
    result['properties']={k:v for k,v in properties.items() if k in required or k=='risk'}
    return result


def bounded_request_prompt(prompt,budget=8500):
    """Bound optional planning context, never clip current metrics or controls."""
    if prompt.get('task')=='operational_supervision':
        from .supervision_context import compact_supervision_prompt
        return compact_supervision_prompt(prompt,budget=min(budget,6500))
    import copy,json
    result=copy.deepcopy(prompt)
    size=lambda:len(json.dumps(result,ensure_ascii=False,separators=(',',':')))
    if size()<=budget:return result
    result=context_retry_prompt(result)
    # Keep current observations at top level; duplicated world/context summaries
    # and retired model prose are not evidence for this screen.
    for key in ('human_example_instruction','human_examples','historical_battle_experience','temporal_evidence','exploration_objective','operator_guidance_rule','historical_context_rule','progress_test_instruction','method_selection','anticipation'):
        result.pop(key,None)
    world=result.get('world_observation')
    if isinstance(world,dict):
        result['world_observation']={k:world[k] for k in ('metrics','metric_changes','identity_rule','last_known_metrics') if k in world}
        result['world_observation']['objects']=[{k:v for k,v in o.items() if k in ('id','legend','offset','identity','appearance_memory')} for o in world.get('objects',[])[:20]]
    knowledge=result.get('source_knowledge')
    if isinstance(knowledge,dict):
        result['source_knowledge']={k:v for k,v in knowledge.items() if k in ('edition','rules')}
    memory=result.get('memory')
    if isinstance(memory,dict):result['memory']={k:v for k,v in memory.items() if k=='current_interface'}
    if size()>budget:
        # Preserve independent OCR, current metrics, spatial offsets and object
        # IDs. Discard optional unverified prose rather than cut evidence JSON.
        for key in ('source_knowledge','recent_history','memory','recent_contact_observation','self_improvement','public_references','retrieved_experiences','learned_policy'):
            result.pop(key,None)
        world=result.get('world_observation',{})
        for obj in world.get('objects',[]):obj.pop('appearance_memory',None)
    return result
