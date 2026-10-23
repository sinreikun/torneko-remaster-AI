"""Reconcile proposed input against current, independently tracked geometry."""
from dataclasses import replace
import re


def reconcile(decision,world,state,native,cfg):
    intent=decision.intent or {};objects=(world or {}).get('objects',[])
    target=next((o for o in objects if o.get('id')==intent.get('target_id')),None)
    report={}
    def wait(reason):
        return replace(decision,keys=[],forecast={},reason=reason,subgoal='現在の対象と位置を確認する',goal_success='現在観測した対象IDと行動が一致する'),{'status':'unverified_target','reason':reason,'original_keys':decision.keys,'intent':intent}
    if intent.get('target_id') and target is None:
        return wait('指定対象は現在の観測に存在しません。過去の位置への操作を保留します。')
    # Searching for stairs remains a legitimate goal, without claiming their presence.
    claim=re.search(r'階段.{0,8}(?:へ移動|に近づ|を降り|を下り|に向か)',decision.subgoal+decision.reason)
    confirmed=any(o.get('legend')=='stair_indicator' for o in objects)
    dialog=any(re.search(r'階段|降ります|下ります',l.get('text','')) for l in (native or {}).get('lines',[]))
    if (intent.get('kind')=='descend' or claim) and not confirmed and not dialog:
        return wait('現在の階段位置を確認できません。探索目標と現在の観測を分離して再判断します。')
    if not target or decision.scene!='gameplay' or not decision.keys:return decision,report
    offset=target.get('offset');m=(state or {}).get('minimap',{})
    if m.get('status')!='observed' or not isinstance(offset,list) or len(offset)!=2:return decision,report
    dx,dy=offset;kind=intent.get('kind')
    if kind not in ('approach','attack','talk','descend'):return decision,report
    if kind in ('attack','talk') and max(abs(dx),abs(dy))<=1:
        # Facing cannot be deduced from position. Never manufacture an attack direction.
        return decision,report
    if not (dx or dy):return decision,report
    # Only repair a single-axis move with independently observed neighboring floor.
    desired=('d' if dx>0 else 'a') if abs(dx)>abs(dy) else ('s' if dy>0 else 'w')
    vectors={'w':(0,-1),'a':(-1,0),'s':(0,1),'d':(1,0)}
    if decision.keys==[desired]:return decision,report
    if not set(vectors)<=set(cfg.allowed_keys):return decision,report
    player=m.get('player_cell');known={tuple(c) for c in m.get('accumulated_cells',[])}
    step=vectors[desired]
    if not player or (player[0]+step[0],player[1]+step[1]) not in known:
        return wait('対象方向と入力が不一致で、次の一マスの通行候補も未確認です。経路を再判断します。')
    updated={'kind':'approach','target_id':target['id']}
    return replace(decision,keys=[desired],intent=updated,forecast={},subgoal=f"{target['id']}へ接近する",reason=f'現在の相対位置({dx},{dy})と既知の隣接マスから{desired}で一マス接近する。',goal_success='対象との距離が減ったことを次の観測で確認する'),{'status':'corrected','original_keys':decision.keys,'intent':intent,'target_offset':offset,'selected_keys':[desired]}
