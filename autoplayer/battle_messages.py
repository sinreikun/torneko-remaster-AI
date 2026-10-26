"""Explicit, frame-linked combat messages; no hit inference from a submitted key."""
import re
from .interfaces import compact_text

NAME=r'[ぁ-んァ-ヶ一-龯ー]{1,24}'
PATTERNS=(
 ('damage_dealt',re.compile(r'トルネコは('+NAME+r')に(\d{1,4})ポイントのダメージを与えた')),
 ('damage_received',re.compile(r'トルネコは('+NAME+r')から(\d{1,4})ポイントのダメージを受けた')),
 ('defeated_enemy',re.compile('('+NAME+r')をやっつけた')),
 ('miss',re.compile(r'トルネコの攻撃は('+NAME+r')に(?:当たらなかった|あたらなかった|外れた|はずれた)')),
)

def read_messages(native,expected_sha):
    if not expected_sha or not native or native.get('status')!='ok' or native.get('frame_sha256')!=expected_sha:return []
    text=compact_text('\n'.join(line.get('text','') for line in native.get('lines',[])))
    out=[]
    for kind,pattern in PATTERNS:
        for match in pattern.finditer(text):
            item={'kind':kind,'enemy':match[1],'quote':match[0],'source_frame_sha256':expected_sha,'source':'explicit_ocr_message','unverified_ocr':True}
            if kind in ('damage_dealt','damage_received'):item['damage']=int(match[2])
            out.append(item)
    # Some live messages omit the actor/target and split the enemy from damage.
    # Preserve unknown outgoing identity rather than inventing a species.
    lines=[compact_text(line.get('text','')) for line in native.get('lines',[])]
    def add(kind,enemy,damage,quote):
        if any(e['kind']==kind and quote.rstrip('。.!！') in e['quote'] for e in out):return
        out.append({'kind':kind,'enemy':enemy,'damage':damage,'quote':quote,'source_frame_sha256':expected_sha,
            'source':'explicit_ocr_message','unverified_ocr':True,'enemy_name_status':'read' if enemy else 'not_stated'})
    for i,line in enumerate(lines):
        hit=re.fullmatch(r'(\d{1,4})ポイントのダメージを与えた[。.!！]?',line)
        if hit:add('damage_dealt',None,int(hit[1]),line)
        miss=re.fullmatch(r'トルネコの攻撃は(?:はずれ(?:てしまった|た)|外れ(?:てしまった|た)|当たらなかった|あたらなかった)[。.!！]?',line)
        if miss:add('miss',None,None,line)
        for quote in (line,line+(lines[i+1] if i+1<len(lines) else '')):
            received=re.fullmatch('('+NAME+r')から(\d{1,4})ポイントのダメージを受けた[。.!！]?',quote)
            if received and not received[1].startswith('トルネコは'):
                add('damage_received',received[1],int(received[2]),quote)
    return out
