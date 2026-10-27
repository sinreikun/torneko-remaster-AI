"""Bound UI rendering work without changing the full diagnostic/learning records."""
import json


def display_json(value,limit=10000):
    def compact(item,depth=0):
        if isinstance(item,str):return item[:600]+('…' if len(item)>600 else '')
        if item is None or type(item) in (bool,int,float):return item
        if depth>=5:return '…'
        if isinstance(item,dict):
            result={str(k)[:100]:compact(v,depth+1) for k,v in list(item.items())[:24]}
            if len(item)>24:result['表示省略']=len(item)-24
            return result
        if isinstance(item,(list,tuple)):
            result=[compact(v,depth+1) for v in item[:8]]
            return result+(['… 残り'+str(len(item)-8)+'件'] if len(item)>8 else [])
        return repr(item)[:200]
    text=json.dumps(compact(value),ensure_ascii=False,indent=2)
    return text if len(text)<=limit else text[:limit]+'\n…（表示を省略。全文は診断ログに保存）'
