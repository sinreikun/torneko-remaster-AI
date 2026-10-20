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
    return out
