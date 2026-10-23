"""Prompt boundaries: historical dynamic facts never become current state."""
import copy
import hashlib
import re

_DYNAMIC=re.compile(r'^(?:hp|recorded_hp|current_hp|metrics|floor|fullness|strength|sword|shield|player_position|player_cell|enemies|contacts|relative_contacts|minimap_observation|map_pose|spatial_observation|world_observation|anticipation)$',re.I)

def historical(value):
    if isinstance(value,dict):
        if re.search(r'HP|満腹|階数|ちから|剣の強さ|盾の強さ',str(value.get('key','')),re.I):
            return {'key':value['key'],'value':'過去の変動値は現在値との混同防止のため省略','historical_only':True}
        return {k:historical(v) for k,v in value.items() if not _DYNAMIC.fullmatch(k)}
    if isinstance(value,list):return [historical(v) for v in value]
    if isinstance(value,str):
        value=re.sub(r'(?i)HP\s*(?:は|が|[:：])?\s*\d+\s*/\s*\d+','[過去のHP記述を省略]',value)
        value=re.sub(r'(?:満\s*腹\s*度|満腹率)\s*(?:は|が|[:：])?\s*\d+\s*[%％]','[過去の満腹度を省略]',value)
        value=re.sub(r'\d+\s*階','[過去の階数を省略]',value)
        return value
    return copy.deepcopy(value)


def current(png,native,engine):
    linked=bool(native and native.get('frame_sha256')==hashlib.sha256(png).hexdigest())
    metrics=engine.reconcile(native)['metrics'] if linked else {}
    return {'source':'current_screen','frame_sha256':hashlib.sha256(png).hexdigest(),
        'captured_at':(native or {}).get('captured_at'),'ocr_status':native.get('status') if linked else 'frame_mismatch',
        'metrics':metrics,'unknown_fields':[k for k in ('hp','floor','fullness','strength','sword','shield') if k not in metrics],
        'rule':'現在画像が最新の観測。読めない値は不明で、履歴・お手本・過去の目標の値で補完しない。OCRも誤読し得るため画像と食い違えば再観測する。履歴を根拠に現在画面を誤りと扱わない。'}
