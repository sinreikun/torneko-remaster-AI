"""Transactional experience memory. Model claims are explicitly provisional."""
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from contextlib import contextmanager

class LearningConnection(sqlite3.Connection):
    batch_depth=0
    batch_bad=False
    def commit(self):
        if not self.batch_depth:return super().commit()
    def __exit__(self,kind,value,traceback):
        if self.batch_depth:
            if kind:self.batch_bad=True;self.rollback()
            return False
        return super().__exit__(kind,value,traceback)

class MemoryStore:
    def __init__(self,path,settings):
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        signature=json.dumps([settings.profile_id,sorted(settings.allowed_keys),sorted(settings.key_bindings.items())] if settings.profile_id else [settings.window_title,settings.allowed_keys,settings.goal,settings.knowledge],ensure_ascii=False)
        self.profile=hashlib.sha256(signature.encode()).hexdigest()
        self.last_row=None;self.settings=settings
        try:
            self.db=sqlite3.connect(path,factory=LearningConnection)
            self.db.execute('PRAGMA journal_mode=WAL')
            if self.db.execute('PRAGMA quick_check').fetchone()[0]!='ok': raise sqlite3.DatabaseError('integrity check failed')
        except sqlite3.DatabaseError as error:
            if hasattr(self,'db'): self.db.close()
            if getattr(error,'sqlite_errorcode',None) not in (sqlite3.SQLITE_CORRUPT,sqlite3.SQLITE_NOTADB) and str(error)!='integrity check failed': raise
            # Preserve the database AND WAL before rebuilding; do not reinterpret a locked/full disk as corruption.
            suffix='.broken-'+str(time.time_ns())
            original=Path(path)
            for tail in ('','-wal','-shm'):
                candidate=Path(str(original)+tail)
                if candidate.exists(): candidate.rename(Path(str(original)+suffix+tail))
            self.db=sqlite3.connect(path,factory=LearningConnection)
            self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS experiences(id INTEGER PRIMARY KEY, profile TEXT NOT NULL, time REAL NOT NULL, row TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS experiences_profile ON experiences(profile,id);
        CREATE TABLE IF NOT EXISTS claims(profile TEXT NOT NULL,key TEXT NOT NULL,value TEXT NOT NULL,confidence REAL NOT NULL,evidence TEXT NOT NULL,time REAL NOT NULL,PRIMARY KEY(profile,key));
        CREATE TABLE IF NOT EXISTS transitions(profile TEXT NOT NULL,origin TEXT NOT NULL,action TEXT NOT NULL,destination TEXT NOT NULL,count INTEGER NOT NULL,PRIMARY KEY(profile,origin,action,destination));
        CREATE TABLE IF NOT EXISTS plans(profile TEXT PRIMARY KEY,steps TEXT NOT NULL,progress TEXT NOT NULL);
        ''')
    def remember(self,row,decision=None):
        if row.get('learning') is False or str(row.get('event','')).startswith('diagnostic'):raise ValueError('診断記録は攻略記憶に保存できません。')
        with self.db:
            if self.last_row and row.get('scene') and self.last_row.get('scene'):
                previous=self.last_row
                action=json.dumps(previous.get('keys',[]) if previous.get('executed') else [],ensure_ascii=False)
                self.db.execute('INSERT INTO transitions VALUES(?,?,?,?,1) ON CONFLICT(profile,origin,action,destination) DO UPDATE SET count=count+1',
                    (self.profile,previous.get('state_key') or previous['scene'],action,row.get('state_key') or row['scene']))
            self.db.execute('INSERT INTO experiences(profile,time,row) VALUES(?,?,?)',(self.profile,time.time(),json.dumps(row,ensure_ascii=False)))
            # Dry runs are observations, never evidence that a proposed action worked.
            if decision and not row.get('dry_run'):
                for claim in decision.memory_updates:
                    self.db.execute('INSERT OR REPLACE INTO claims VALUES(?,?,?,?,?,?)',(self.profile,claim['key'],claim['value'],claim['confidence'],decision.observation,time.time()))
                if decision.plan or decision.progress:
                    prior=self.context()
                    self.db.execute('INSERT OR REPLACE INTO plans VALUES(?,?,?)',(self.profile,json.dumps(decision.plan or prior['plan'],ensure_ascii=False),json.dumps(list(dict.fromkeys(prior['reported_progress']+decision.progress))[-30:],ensure_ascii=False)))
        self.last_row=dict(row)
    def history(self):
        rows=self.db.execute('SELECT row FROM experiences WHERE profile=? ORDER BY id DESC LIMIT 12',(self.profile,)).fetchall()
        return [json.loads(r[0]) for r in reversed(rows)]
    def context(self):
        plan=self.db.execute('SELECT steps,progress FROM plans WHERE profile=?',(self.profile,)).fetchone()
        claims=self.db.execute('SELECT key,value,confidence,evidence FROM claims WHERE profile=? ORDER BY time DESC LIMIT 40',(self.profile,)).fetchall()
        recent=self.history()
        ineffective=[{'scene':r.get('scene'),'keys':r.get('keys'),'observation':r.get('observation')} for r in recent if r.get('executed') and r.get('changed') is False]
        transitions=self.db.execute('SELECT origin,action,destination,count FROM transitions WHERE profile=? ORDER BY count DESC LIMIT 10',(self.profile,)).fetchall()
        knowledge_state=next((r for r in reversed(recent) if 'autonomous_goal' in r),{})
        learning={}
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='policy_actions'").fetchone():
            from .policy import profile_for
            counts=self.db.execute('SELECT COUNT(*),COALESCE(SUM(trials),0),COALESCE(SUM(positive),0) FROM policy_actions WHERE profile=?',(profile_for(self.settings),)).fetchone()
            learning={'state_actions':counts[0],'input_trials':counts[1],'positive_progress_observations':counts[2],'note':'永続方策の統計。診断試験を成功経験には加算しません。'}
        return {'policy_learning':learning,**{k:knowledge_state[k] for k in ('autonomous_goal','knowledge_revisions','knowledge_catalog_sha256') if k in knowledge_state},'known_transitions':[dict(origin=r[0],keys=json.loads(r[1]),destination=r[2],count=r[3],attribution='observed_association_not_causal') for r in transitions], 'plan':json.loads(plan[0]) if plan else [],'reported_progress':json.loads(plan[1]) if plan else [],
                'provisional_claims':[dict(zip(('key','value','confidence','visual_evidence'),r)) for r in claims],
                'unconfirmed_input_responses':ineffective,'note':'Claims and progress are model reports, not verified facts. Recheck against the current screen.'}
    def recall(self,native,goal):
        """Retrieve observations, not old model captions, for the current screen."""
        import re
        def terms(text):
            text=re.sub(r'\s+','',text.casefold())
            return set(text) | set(text[i:i+2] for i in range(max(0,len(text)-1)))
        query=terms(' '.join(l.get('text','') for l in native.get('lines',[])))
        if not query:return []
        candidates=[]
        for (raw,) in self.db.execute('SELECT row FROM experiences WHERE profile=? ORDER BY id DESC LIMIT 256',(self.profile,)):
            row=json.loads(raw)
            if row.get('dry_run') or row.get('learning') is False:continue
            observed=row.get('ocr',{})
            quotes=[l.get('text','')[:160] for l in observed.get('lines',[])[:12]]
            overlap=len(query & terms(' '.join(quotes)))
            if not overlap:continue
            candidates.append((overlap,{'ocr_quotes':quotes,'keys':row.get('observed_keys',[]),
                'hold_ms':row.get('hold_ms'),'executed':row.get('executed',False),'changed':row.get('changed'),
                'attribution':row.get('transition_attribution','unverified'),
                'proposed_goal':row.get('autonomous_goal'),'goal_status':'unverified_model_proposal'}))
        candidates.sort(key=lambda item:item[0],reverse=True)
        return [r for _,r in candidates[:3]]

    @contextmanager
    def learning_batch(self):
        """One feedback transaction; never defer the input journal."""
        outer=self.db.batch_depth==0
        if outer:self.db.batch_bad=False;self.last_batch_commit_ms=0.
        self.db.batch_depth+=1
        try:yield
        except BaseException:
            self.db.batch_bad=True;self.db.rollback();raise
        finally:self.db.batch_depth-=1
        if outer:
            if self.db.batch_bad:raise RuntimeError('学習の一括更新が失敗したため保存を取り消しました。')
            started=time.monotonic()
            sqlite3.Connection.commit(self.db)
            self.last_batch_commit_ms=(time.monotonic()-started)*1000

    def close(self): self.db.close()
