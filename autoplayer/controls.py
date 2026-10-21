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
    'minimap':{'blue':'当初の青色を含む、各階層の配色で表示される既知領域','yellow_square':'自分の位置','red_dot':'敵の位置',
        'note':'色付きの領域全体はミニマップという表示であり、近づく対象物ではない。階層で地図の色・床・壁の模様が変わる。同じ色を同じ地形と決め付けない。未知領域は壁か未探索か未確定。'}}

def is_torneko(title):return any(word in title.casefold() for word in ('トルネコ','torneko'))

# User-confirmed display modifier; not a turn/progress or compatibility test.
REFERENCE["minimap"]["hold_num7"]="num7を押している間はミニマップとHUDのみ表示。専用観測後は必ず離して通常画面へ戻す。"

REFERENCE["minimap"]["menu_toggle"]="通常メニュー初期位置からnum0→右(d)→num1で表示オン/オフを切替。オンと検出した時だけ1回実行し、マップ項目が残る場合はnum2で閉じる。"

# User preference for ordinary conversations; destructive confirmations stay blocked.
REFERENCE['dialogue_policy']='会話・ダイアログの決定待ちはnum1を1回押して進める。通常の選択肢は基本的に「はい」を選ぶ。「はい」が選択済みならnum1、未選択なら現在のカーソルと選択肢の配置に基づく方向キー(w/a/s/d)を1回だけ送り、次の画面で選択を確認してからnum1。選択肢の存在・配置・現在選択を推測しない。num2で会話を飛ばしたり「いいえ」を選ばない。セーブ削除・初期化・購入・ゲーム終了など既存の破壊的操作禁止は維持する。'
