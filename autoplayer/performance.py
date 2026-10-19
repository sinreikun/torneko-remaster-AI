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
        'active_goal':{k:v for k,v in goal.items() if k in ('objective','success_condition','reason')} if isinstance(goal,dict) else goal,
        'ocr':ocr_context(value.get('ocr',{})),
        'facts':[{k:f[k] for k in ('id','summary','editions','sources','applicability','verification') if k in f} for f in selected],
        'sources':[{k:s[k] for k in ('id','url') if k in s} for s in value['sources'] if s['id'] in ids],
        'rules':'出典とOCRは未検証の参考。版違いは仮説。用語の一致で効果や達成を確定しない。全体目標を変更しない。'}


def fast_policy_ready(state,stable,anchor,floor,objective,count,blocked,stagnant,reassess):
    return bool(anchor and count<3 and stable and state and state['eligible']
        and state['metrics'].get('floor',{}).get('values')==floor and state['objective']==objective
        and state['objective']!='survive' and not state['minimap'].get('enemies')
        and not blocked and stagnant<2 and not reassess)


# Keep obsolete display-related prose out of planning context.
import re
REFERENCES=re.compile(r'ミニマップ|地図|マップ|minimap|\bmap\b|青い(?:物体|図形|表示|箱|四角|領域|ブロック)|黄色.{0,12}(?:四角|円形|マーカー)|赤い点|blue[_ ](?:object|box|shape|display)|yellow[_ ](?:square|circle)|red[_ ]dot',re.I)
PRIVATE_KEYS={'minimap','minimap_pixels','minimap_observation','player_cell','cell','map_status','_floor_observed'}

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
