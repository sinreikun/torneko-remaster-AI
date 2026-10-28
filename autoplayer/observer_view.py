"""Bounded observer-facing summaries; no game decisions or inferred successes."""
from collections import deque
from .presentation import display_json

SECTIONS={
    '予測と結果':('観察ダイジェスト','結果'),
    '目的・手順':('目的','運用計画','選択行動と理由'),
    '学習・仮説':('行動学習','自己改善','仮説'),
    '候補比較':('候補と評価',),
    '認識・知識':('現在状態','OCR・攻略知識の照合'),
    '接続・操作':('バックエンド状態','キー配置・ミニマップ'),
}
ALIASES={'新発見':'仮説','修正履歴':'仮説','系列のパターン':'行動学習','累積統計':'行動学習','手法の選択・学習':'行動学習','背景適合性':'バックエンド状態','ゲームの推定':'現在状態'}

class ObserverPanels:
    def __init__(self):self.values={}
    def put(self,title,value):
        destination=ALIASES.get(title,title)
        if destination not in {x for group in SECTIONS.values() for x in group}:return None
        text=value[:10000] if isinstance(value,str) else display_json(value,10000)
        self.values.setdefault(destination,{})[title]=text
        return destination
    def render(self,section):
        parts=[]
        for title in SECTIONS.get(section,()):
            entries=self.values.get(title,{})
            text='\n\n'.join((name+'\n' if name!=title else '')+value for name,value in entries.items())
            parts.append('【'+title+'】\n'+(text or '観測待ち'))
        return '\n\n'.join(parts)[:24000]

class ObserverDigest:
    def __init__(self):self.current={};self.history=deque(maxlen=6);self.last_result='結果の観測待ち'
    @staticmethod
    def short(value,limit=650):
        return value[:limit] if isinstance(value,str) else display_json(value,limit)
    def update(self,row):
        event=row.get('event')
        if event not in ('decision','sent','result','human_console_result'):return False
        if event in ('decision','sent'):
            operation=row.get('operation') or {}
            forecast=(row.get('forecast') or {}).get('first_expected') or (row.get('action_prediction') or {}).get('expected')
            self.current={'pending':True,'step':row.get('step','?'),'purpose':self.short(row.get('subgoal') or operation.get('objective') or '未設定',300),
              'keys':row.get('keys',[]),'reason':self.short(row.get('reason','未設定')),'stage':operation.get('execution_stage') or row.get('execution_stage') or operation.get('skill') or '未設定',
              'expected':self.short(forecast or operation.get('expected_signal') or '予測未設定')}
        else:
            validation=row.get('prediction_validation') or row.get('forecast_evaluation') or {}
            status=validation.get('status','未検証')
            status={'matched':'予測一致','mismatch':'予測不一致','unestimated':'予測なし'}.get(status,status)
            evaluation=row.get('operation_evaluation') or {}
            result=f"手 {row.get('step','?')} / 入力 {row.get('observed_keys',row.get('keys',[]))}\n予測照合: {status}\n実行: {row.get('executed','未確認')} / 画面変化: {row.get('changed','未確認')}"
            if 'progress' in evaluation:result+='\n進展信号: '+str(evaluation['progress'])+'（目標達成とは別）'
            if row.get('policy_learning',{}).get('reward') is not None:result+='\n観測報酬: '+str(row['policy_learning']['reward'])
            expected=self.current.get('expected','予測未設定') if self.current.get('step')==row.get('step') else '当該手の入力前予測は表示未取得'
            if self.current.get('step')==row.get('step'):self.current['pending']=False
            self.last_result=result;self.history.append(result+'\n入力前の予測: '+expected)
        return True
    def summary(self):
        c=self.current
        label='次の入力' if c.get('pending') else '直近の入力'
        return f"目的: {c.get('purpose','未設定')}\n\n手 {c.get('step','?')} / {label}: {c.get('keys',[])}\n手順: {c.get('stage','未設定')}\n根拠: {c.get('reason','観測待ち')}\n\n期待: {c.get('expected','予測未設定')}\n\n直前の観測結果\n{self.last_result}"
    def journal(self):
        return '入力送信・画面変化・進展・目標達成は別の判定です。\n\n'+'\n\n────────\n\n'.join(self.history) if self.history else '操作結果を取得すると、直近6手の入力・予測・結果を表示します。'
