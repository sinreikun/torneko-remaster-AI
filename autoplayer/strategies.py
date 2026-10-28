"""Screen-only hypotheses and bounded method selection, never code self-modification."""
import json
import math
import re
import time
import unicodedata

GENRES=('roguelike','rpg','action','adventure','puzzle','strategy','simulation','racing','sports','unknown')
TEMPOS=('turn_based','real_time','mixed','unknown')
METHODS=('grounded_text','structured_plan','temporal_review','visual_replan')
GUIDANCE={
    'grounded_text':'観察報告と現在OCRだけから、根拠のある短い操作を1つ計画する。',
    'structured_plan':'現在の事実、未確認の仮説、目標の前提条件を分ける。既に満たした条件を目標にせず、次の前提条件を満たす1操作を選ぶ。',
    'temporal_review':'前回と現在のOCR・送信した操作・中立観測を比較し、反復の原因仮説を見直す。入力後の変化を因果や成功と断定しない。',
    'visual_replan':'同送した現在画像を観察報告・OCRと照合する。履歴の物体や選択肢を現在画像に補完せず、食い違いがあれば無入力で再観測する。'}
GENRE_HINTS={
    'roguelike':'生存と資源を優先し、可視の地形と既訪問位置に基づき探索する。',
    'rpg':'会話・クエスト・戦闘の前提関係と資源を整理する。',
    'action':'操作遅延と移動・危険を比較し、精密な実時間制御に足る観測があるか再評価する。',
    'adventure':'可視の会話・手掛かり・アイテム用途を照合し、未解決の前提条件を探す。',
    'puzzle':'可視盤面のルールと制約を確認し、未確認のルールを断定しない。',
    'strategy':'可視の資源、ユニット、配置と目標の前提条件を整理する。',
    'simulation':'ゲーム内の可視目標と制約を探し、終了条件が不明なら暫定目標として扱う。',
    'racing':'コース、進行方向、速度、周回表示を確認する。観測速度不足なら無理に操作しない。',
    'sports':'得点、時間、操作対象、ルールを可視情報から確認する。',
    'unknown':'ジャンルを決め付けず、操作案内とゲーム内の目的を探す。'}


def normalized(text):
    return re.sub(r'\s+','',unicodedata.normalize('NFKC',text).casefold())


def visible_text(native):
    return normalized('\n'.join(line.get('text','') for line in native.get('lines',[]))) if native.get('status')=='ok' else ''


def progress_signal(test,before,after):
    """A frozen, literal text transition is a subgoal signal, not verified game victory."""
    if not isinstance(test,dict) or test.get('kind') not in ('text_appears','text_disappears'):
        return None
    target=normalized(test.get('text',''))
    first,last=visible_text(before),visible_text(after)
    # Missing OCR is neither disappearance nor negative evidence.
    if len(target)<2 or not first or not last:return None
    if test['kind']=='text_appears':return target not in first and target in last
    return target in first and target not in last


class MethodLearner:
    def __init__(self,memory):
        self.memory=memory;self.db=memory.db;self.read_only=False
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS method_stats(profile TEXT,scene TEXT,method TEXT,
                trials INTEGER,valid INTEGER,signals INTEGER,elapsed_ms REAL,
                PRIMARY KEY(profile,scene,method));
            CREATE TABLE IF NOT EXISTS game_hypotheses(profile TEXT PRIMARY KEY,data TEXT);
            CREATE TABLE IF NOT EXISTS method_events(id INTEGER PRIMARY KEY,profile TEXT,time REAL,data TEXT);
        ''')
        self.scene='unknown';self.current='grounded_text';self.without_signal=0
        self.cooldown={};self.tick=0;self.last_outcome=None

    def hypothesis(self,observation):
        proposed=observation.get('game_hypothesis',{})
        if not isinstance(proposed,dict):proposed={}
        genre=proposed.get('genre','unknown');tempo=proposed.get('tempo','unknown')
        if genre not in GENRES:genre='unknown'
        if tempo not in TEMPOS:tempo='unknown'
        for field in ('objectives','evidence'):
            if not isinstance(proposed.get(field,[]),list):proposed={**proposed,field:[]}
        value={'genre':genre,'tempo':tempo,'objectives':[s[:160] for s in proposed.get('objectives',[])[:4] if isinstance(s,str)],
               'evidence':[s[:160] for s in proposed.get('evidence',[])[:4] if isinstance(s,str)],
               'status':'model_hypothesis_unverified','note':'ジャンルや方向性は画面由来の仮説。ルール・クリア条件の保証ではない。'}
        if not self.read_only:
            with self.db:self.db.execute('INSERT OR REPLACE INTO game_hypotheses VALUES(?,?)',(self.memory.profile,json.dumps(value,ensure_ascii=False)))
        return value

    def select(self,observation,stalled=False,has_history=False):
        self.tick+=1;scene=observation.get('scene_type','unknown')
        hypothesis=self.hypothesis(observation)
        bucket=scene+':'+hypothesis['genre']
        new_scene=bucket!=self.scene
        if new_scene:
            self.scene=bucket;self.current='grounded_text';self.without_signal=0
        available=[m for m in METHODS if m!='temporal_review' or has_history]
        stats={r[0]:r[1:] for r in self.db.execute('SELECT method,trials,valid,signals,elapsed_ms FROM method_stats WHERE profile=? AND scene=?',
                                                  (self.memory.profile,self.scene))}
        total=sum(r[0] for r in stats.values())+1
        def score(method):
            trials,valid,signals,elapsed=stats.get(method,(0,0,0,0))
            return (signals+.2*valid)/(trials+1)+math.sqrt(2*math.log(total+1)/(trials+1))-.05*elapsed/max(1,trials)/10000
        switched=False
        if new_scene and stats:self.current=max(available,key=score)
        if stalled or self.without_signal>=3:
            self.cooldown[(self.scene,self.current)]=self.tick+4
            choices=[m for m in available if self.cooldown.get((self.scene,m),0)<=self.tick and m!=self.current]
            if not choices:choices=[m for m in available if m!=self.current]
            self.current=max(choices,key=score) if choices else 'grounded_text'
            self.without_signal=0;switched=True
        return {'method':self.current,'scene_bucket':self.scene,'switched':switched,
                'game_hypothesis':hypothesis,'genre_guidance':GENRE_HINTS[hypothesis['genre']],
                'instruction':GUIDANCE[self.current],'last_outcome':self.last_outcome,
                'note':'手法の評価は応答妥当性・事前条件の可視変化・推論時間。ゲームクリアの成功率ではない。'}

    def record(self,selection,valid,elapsed_ms=0,signal=None,reason='',eligible=True):
        if self.read_only or not selection or not eligible:return
        scene=selection['scene_bucket'];method=selection['method']
        signal_count=int(signal is True)
        with self.db:
            self.db.execute('''INSERT INTO method_stats VALUES(?,?,?,1,?,?,?)
                ON CONFLICT(profile,scene,method) DO UPDATE SET trials=trials+1,valid=valid+excluded.valid,
                    signals=signals+excluded.signals,elapsed_ms=elapsed_ms+excluded.elapsed_ms''',
                (self.memory.profile,scene,method,int(valid),signal_count,elapsed_ms))
            value={'method':method,'scene_bucket':scene,'valid':valid,'frozen_criterion_observed':signal,
                   'reason':reason,'game_success_verified':False}
            self.db.execute('INSERT INTO method_events(profile,time,data) VALUES(?,?,?)',(self.memory.profile,time.time(),json.dumps(value,ensure_ascii=False)))
        self.last_outcome=value
        self.without_signal=0 if signal is True else self.without_signal+1

    def context(self):
        saved=self.db.execute('SELECT data FROM game_hypotheses WHERE profile=?',(self.memory.profile,)).fetchone()
        stats=[dict(zip(('scene_bucket','method','trials','valid','criterion_observations','elapsed_ms'),r)) for r in self.db.execute(
            'SELECT scene,method,trials,valid,signals,elapsed_ms FROM method_stats WHERE profile=? ORDER BY trials DESC LIMIT 12',(self.memory.profile,))]
        return {'game_hypothesis':json.loads(saved[0]) if saved else {'genre':'unknown','tempo':'unknown','status':'not_observed'},
                'methods':stats,'last_outcome':self.last_outcome,'consecutive_without_signal':self.without_signal}
