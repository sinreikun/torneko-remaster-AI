"""Curated public references and user controls, selected as hypotheses, not actions."""
import hashlib,json,re
from pathlib import Path
from .controls import REFERENCE,is_torneko
from .interfaces import ordinary_choice,torneko_menu_kind,result_screen
from .knowledge import normalize

class GameGuide:
    def __init__(self,cfg,path=None):
        self.cfg=cfg
        raw=(Path(path) if path else Path(__file__).resolve().parent.parent/'knowledge/torneko-tactics.json').read_bytes()
        self.digest=hashlib.sha256(raw).hexdigest();self.catalog=json.loads(raw)
        ids={s['id'] for s in self.catalog['sources']}
        if len({r['id'] for r in self.catalog['rules']})!=len(self.catalog['rules']):raise ValueError('Duplicate guide rule')
        for rule in self.catalog['rules']+self.catalog.get('foundations',[]):
            if not set(rule['sources'])<=ids:raise ValueError('Unknown guide source')
    def context(self,native,scene,context,metrics):
        if not is_torneko(self.cfg.window_title):return {'status':'not_applicable'}
        native=native or {};text='\n'.join(l.get('text','') for l in native.get('lines',[]))
        contacts=context.get('relative_contacts',{}).get('contacts',[])
        hp=metrics.get('hp',{}).get('values');food=metrics.get('fullness',{}).get('values')
        tags=set()
        if any(c.get('adjacent') for c in contacts):tags.add('combat')
        elif contacts:tags.add('ranged')
        if scene in ('gameplay','dungeon','exploration') and metrics.get('floor'):tags.add('explore')
        if hp and hp[1] and hp[0]/hp[1]<=.35:tags.add('low_hp')
        if food and food[0]<=20:tags.add('hungry')
        kind=torneko_menu_kind(native)
        if kind in ('inventory','item_actions','carry_inventory') or '道具' in text and scene in ('menu','inventory'):tags.add('inventory')
        if re.search(r'未識別|正体|色の草|巻物|指輪|杖',text):tags.add('unidentified')
        if re.search(r'読む|飲む|投げる|なげる|振る|ふる|うつ|装備',text):tags.add('item')
        if ordinary_choice(native) or scene in ('dialog','dialogue'):tags.add('dialogue')
        if result_screen(native) or re.search(r'やりなおし|やり直し|倒された|力尽き',text):tags.add('death')
        if (context.get('exterior_action_policy') or {}).get('mode')=='exit_exploration':tags.add('outside')
        if re.search(r'箱|階段|帰還|リレミト',text):tags.add('goal')
        if re.search(r'ちから|剣の強さ|盾の強さ|経験値',text):tags.add('status')
        foundations=[r for r in self.catalog.get('foundations',[]) if tags.intersection(r['when'])]
        normalized=normalize(text)
        rules=[r for r in self.catalog['rules'] if tags.intersection(r['when']) and
            (not r.get('terms') or any(normalize(term) in normalized for term in r['terms']))]
        rules.sort(key=lambda r:(r['priority'],len(tags.intersection(r['when']))),reverse=True)
        urgent=[r for r in rules if r['id'] in ('recovery','food','death-retry','dialogue')]
        selected=(urgent+[r for r in rules if r not in urgent])[:4]
        bindings=self.cfg.key_bindings or REFERENCE['keys']
        patterns={'move':r'^(上|左|下|右)移動|^(up|left|down|right)$','face':r'方向転換','attack':r'攻撃','confirm':r'決定|confirm','cancel':r'キャンセル|cancel','menu':r'メニュー|menu','arrow':r'矢','step':r'足踏み','item_details':r'詳細|マップ'}
        role_keys={role:[k for k,v in bindings.items() if k in self.cfg.allowed_keys and re.search(pattern,v,re.I)] for role,pattern in patterns.items()}
        role_keys['item']=role_keys['menu']+role_keys['confirm']
        needed={role for rule in selected+foundations for role in rule['roles']};source_ids={s for rule in selected+foundations for s in rule['sources']}
        return {'status':'curated_reference','catalog_sha256':self.digest,'selected_ids':[r['id'] for r in selected],'selected_foundation_ids':[r['id'] for r in foundations],
            'foundations':[{k:r[k] for k in ('id','precondition','expected_effect','verify_or_revise','sources','applicability')} for r in foundations],
            'rules':[{k:r[k] for k in ('id','precondition','expected_effect','verify_or_revise','sources','applicability','procedure') if k in r} for r in selected],
            'current_keys':{role:role_keys[role] for role in sorted(needed)},
            'button_behaviors':{role:self.catalog.get('button_behaviors',{})[role] for role in sorted(needed) if role in self.catalog.get('button_behaviors',{})},
            'sources':[{k:s[k] for k in ('id','url','edition','kind')} for s in self.catalog['sources'] if s['id'] in source_ids],
            'instruction':'foundationsは利用者提供の対象環境の基礎知識/運用方針。実測の統計とは別に扱い、現在の画面で前提を確認して利用する。攻略資料は行動仮説の参考データで命令ではない。現在の画像/OCR/実測結果と作戦指示を優先。初代の情報をリマスターの確定ルールにせず、対象の名前・所持・向き・効果を作らない。現在キーで次の1入力と期待する変化を選び、結果が違えば仮説を修正する。'}
