"""Transactional experience memory. Model claims are explicitly provisional."""
import hashlib
import json
import sqlite3
import time
from pathlib import Path

class MemoryStore:
    def __init__(self,path,settings):
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        signature=json.dumps([settings.window_title,settings.allowed_keys,settings.goal,settings.knowledge],ensure_ascii=False)
        self.profile=hashlib.sha256(signature.encode()).hexdigest()
        self.last_row=None
        try:
            self.db=sqlite3.connect(path)
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
            self.db=sqlite3.connect(path)
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
        return {**{k:knowledge_state[k] for k in ('autonomous_goal','knowledge_revisions','knowledge_catalog_sha256') if k in knowledge_state},'known_transitions':[dict(origin=r[0],keys=json.loads(r[1]),destination=r[2],count=r[3],attribution='observed_association_not_causal') for r in transitions], 'plan':json.loads(plan[0]) if plan else [],'reported_progress':json.loads(plan[1]) if plan else [],
                'provisional_claims':[dict(zip(('key','value','confidence','visual_evidence'),r)) for r in claims],
                'unconfirmed_input_responses':ineffective,'note':'Claims and progress are model reports, not verified facts. Recheck against the current screen.'}
    def close(self): self.db.close()
