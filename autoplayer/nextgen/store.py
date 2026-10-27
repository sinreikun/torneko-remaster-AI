"""Separate schema; one bounded writer. Never imports previous learning scores."""
import copy,json,queue,sqlite3,threading,time
from pathlib import Path

SCHEMA='''
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT);
INSERT OR IGNORE INTO metadata VALUES('generation','2');
CREATE TABLE IF NOT EXISTS states(profile TEXT,key TEXT,data TEXT,visits INTEGER,updated REAL,PRIMARY KEY(profile,key));
CREATE TABLE IF NOT EXISTS transitions(id INTEGER PRIMARY KEY,profile TEXT,session TEXT,step INTEGER,created REAL,data TEXT);
CREATE INDEX IF NOT EXISTS transition_lookup ON transitions(profile,id DESC);
CREATE TABLE IF NOT EXISTS rules(profile TEXT,key TEXT,data TEXT,updated REAL,PRIMARY KEY(profile,key));
CREATE TABLE IF NOT EXISTS appearances(profile TEXT,id TEXT,data TEXT,updated REAL,PRIMARY KEY(profile,id));
CREATE TABLE IF NOT EXISTS interpretations(profile TEXT,id TEXT,data TEXT,updated REAL,PRIMARY KEY(profile,id));
CREATE TABLE IF NOT EXISTS hypotheses(profile TEXT,id TEXT,data TEXT,updated REAL,PRIMARY KEY(profile,id));
CREATE TABLE IF NOT EXISTS graph(profile TEXT,origin TEXT,action TEXT,destination TEXT,data TEXT,updated REAL,PRIMARY KEY(profile,origin,action,destination));
CREATE TABLE IF NOT EXISTS evaluations(id INTEGER PRIMARY KEY,profile TEXT,session TEXT,step INTEGER,kind TEXT,data TEXT);
CREATE INDEX IF NOT EXISTS evaluation_lookup ON evaluations(profile,id DESC);
'''

class ExperienceStore:
    def __init__(self,path,profile,session):
        self.path=Path(path);self.profile=profile;self.session=session
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(self.path) as db:db.executescript(SCHEMA)
        self.reader=sqlite3.connect(self.path)
        self.queue=queue.Queue(maxsize=256);self.stopping=threading.Event();self.error=None;self.commit_ms=0.;self.written=0
        self.thread=threading.Thread(target=self._work,name='world-v2-writer',daemon=True);self.thread.start()
    def load(self,table,key=None,limit=512):
        if table not in ('rules','hypotheses','states','appearances','interpretations'):raise ValueError('Unsupported model table')
        sql=f'SELECT data FROM {table} WHERE profile=?';args=[self.profile]
        if key is not None:sql+=' AND '+('key' if table in ('rules','states') else 'id')+'=?';args.append(key)
        sql+=' ORDER BY updated DESC LIMIT ?';args.append(min(512,limit))
        return [json.loads(r[0]) for r in self.reader.execute(sql,args)]
    def put(self,kind,data,step=0,key=None):
        if self.stopping.is_set():raise RuntimeError('Experience writer is closed')
        item=(kind,json.dumps(data,ensure_ascii=False,separators=(',',':')),step,key,time.time())
        while True:
            self.check()
            try:self.queue.put(item,timeout=.05);return
            except queue.Full:continue # Learning is never silently dropped.
    def check(self):
        if self.error:raise RuntimeError('世界モデルDB保存失敗: '+self.error)
    def _work(self):
        db=sqlite3.connect(self.path)
        try:
            while not self.stopping.is_set() or not self.queue.empty():
                try:first=self.queue.get(timeout=.1)
                except queue.Empty:continue
                batch=[first]
                while len(batch)<64:
                    try:batch.append(self.queue.get_nowait())
                    except queue.Empty:break
                started=time.monotonic()
                with db:
                    for kind,raw,step,key,created in batch:
                        if kind=='transition':db.execute('INSERT INTO transitions(profile,session,step,created,data) VALUES(?,?,?,?,?)',(self.profile,self.session,step,created,raw))
                        elif kind=='states':
                            db.execute('INSERT INTO states VALUES(?,?,?,1,?) ON CONFLICT(profile,key) DO UPDATE SET data=excluded.data,visits=visits+1,updated=excluded.updated',(self.profile,key,raw,created))
                        elif kind in ('rules','hypotheses','appearances','interpretations'):
                            column='key' if kind=='rules' else 'id'
                            db.execute(f'INSERT INTO {kind} VALUES(?,?,?,?) ON CONFLICT(profile,{column}) DO UPDATE SET data=excluded.data,updated=excluded.updated',(self.profile,key,raw,created))
                        elif kind=='graph':
                            value=json.loads(raw)
                            db.execute('INSERT INTO graph VALUES(?,?,?,?,?,?) ON CONFLICT(profile,origin,action,destination) DO UPDATE SET data=excluded.data,updated=excluded.updated',(self.profile,value['origin'],value['action'],value['destination'],raw,created))
                        else:db.execute('INSERT INTO evaluations(profile,session,step,kind,data) VALUES(?,?,?,?,?)',(self.profile,self.session,step,kind,raw))
                self.written+=len(batch);self.commit_ms=(time.monotonic()-started)*1000
                for _ in batch:self.queue.task_done()
        except Exception as error:self.error=repr(error)
        finally:db.close()
    def close(self):
        self.stopping.set();self.thread.join();self.reader.close();self.check()

def diagnostic_summary(db):
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'rules','hypotheses','evaluations','transitions'}<=tables:return None
    return {'generation':2,'counts':{t:db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('transitions','states','rules','hypotheses','interpretations','appearances','evaluations','graph')},
      'recent_transitions':[json.loads(r[0]) for r in db.execute('SELECT data FROM transitions ORDER BY id DESC LIMIT 5')],
      'recent_evaluations':[json.loads(r[0]) for r in db.execute('SELECT data FROM evaluations ORDER BY id DESC LIMIT 40')],
      'interpretations':[json.loads(r[0]) for r in db.execute('SELECT data FROM interpretations ORDER BY updated DESC LIMIT 12')],
      'hypotheses':[json.loads(r[0]) for r in db.execute('SELECT data FROM hypotheses ORDER BY updated DESC LIMIT 20')],
      'note':'観測と予測照合。保存件数は学習能力・攻略成功の証明ではない。'}
