"""User-confirmed Torneko mapping; overrides older inferred controls."""
import hashlib
import json

REVISION='torneko-user-20261019-v1'
ACTIONS={'w':'上移動','a':'左移動','s':'下移動','d':'右移動','num1':'決定／攻撃／話す',
    'num2':'キャンセル／ダッシュ','num5':'方向転換／整理','num0':'メニュー','num4':'矢をうつ',
    'num3':'斜め移動補助','num7':'マップ表示','num6':'足踏み'}
KEYS=list(ACTIONS)
DIGEST=hashlib.sha256(json.dumps(ACTIONS,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
REFERENCE={'revision':REVISION,'source':'ユーザーが提示した現在のキー配置・地図凡例',
    'keys':ACTIONS,'unused_keys':['enter','space'],
    'minimap':{'blue':'視界に入ったことのある領域','yellow_square':'自分の位置','red_dot':'敵の位置',
        'note':'青い表示全体はミニマップという表示であり、近づく対象物ではない。未知領域は壁か未探索か未確定。'}}

def is_torneko(title):return any(word in title.casefold() for word in ('トルネコ','torneko'))

# User-confirmed display modifier; not a turn/progress or compatibility test.
REFERENCE["minimap"]["hold_num7"]="num7を押している間はミニマップとHUDのみ表示。専用観測後は必ず離して通常画面へ戻す。"
