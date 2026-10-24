"""Unverified transfer of observed text responses between similar OCR layouts."""
import hashlib,json,math
from .interfaces import compact_text,PROTECTED_CONFIRMATION,has_text_content
from .experience_review import outcome_learning_allowed


def layout_group(native,size,metrics):
    if not has_text_content(native) or len(size)!=2 or min(size)<=0 or any(metrics.get(k) for k in ('hp','floor')):return None
    lines=[l for l in native.get('lines',[]) if compact_text(l.get('text',''))]
    if not lines or any(not isinstance(l.get('confidence'),(int,float)) or not math.isfinite(l['confidence']) or l['confidence']<.8 for l in lines):return None
    if PROTECTED_CONFIRMATION.search('\n'.join(l.get('text','') for l in lines)):return None
    boxes=[w.get('box',[]) for l in lines for w in l.get('words',[])]
    if not boxes or any(len(b)!=4 or any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in b) or b[2]<=0 or b[3]<=0 or b[0]<0 or b[1]<0 or b[0]+b[2]>size[0] or b[1]+b[3]>size[1] for b in boxes):return None
    labels={compact_text(l['text']).lstrip('▶►▷>→') for l in lines}
    features={'anchor':[int(8*min(b[0] for b in boxes)/size[0]),int(8*min(b[1] for b in boxes)/size[1])],
        'options':sorted(labels&{'はい','いいえ','Yes','No'}),'source':'ocr_geometry_v1'}
    return {'id':hashlib.sha256(json.dumps(features,sort_keys=True).encode()).hexdigest()[:24],**features}


class InterfaceResponses:
    def __init__(self,db,profile):
        self.db=db;self.profile=profile
        self.db.execute('CREATE TABLE IF NOT EXISTS interface_responses_v1(profile TEXT,layout TEXT,action TEXT,data TEXT,PRIMARY KEY(profile,layout,action))');self.db.commit()
    def samples(self,group,key):
        row=self.db.execute('SELECT data FROM interface_responses_v1 WHERE profile=? AND layout=? AND action=?',(self.profile,group,key)).fetchone()
        return json.loads(row[0]) if row else []
    def record(self,before,after,row,neutral):
        if not row.get('executed') or row.get('dry_run') or row.get('turn_kind')=='aborted' or row.get('action_kind')!='input' or (row.get('input_receipt') or {}).get('os_submission')!='accepted' or len(row.get('observed_keys',[]))!=1 or not outcome_learning_allowed(row):return {'status':'excluded_input'}
        group=before.get('interface_layout_group') or {};other=after.get('interface_layout_group') or {}
        if not group or group.get('options') or group.get('id')!=other.get('id') or not neutral or not neutral.get('text_reliable') or (neutral.get('interface_layout_group') or {}).get('id')!=group['id'] or neutral.get('text_key')!=before.get('text_key'):
            return {'status':'excluded_unstable_or_different_layout'}
        key=row['observed_keys'][0];samples=self.samples(group['id'],key)
        sample={'origin':before['text_key'],'changed':before['text_key']!=after['text_key']}
        samples=(samples+[sample])[-32:]
        self.db.execute('INSERT INTO interface_responses_v1 VALUES(?,?,?,?) ON CONFLICT(profile,layout,action) DO UPDATE SET data=excluded.data',(self.profile,group['id'],key,json.dumps(samples)));self.db.commit()
        return {'status':'recorded','layout':group['id'],'samples':len(samples),'text_response':sample['changed'],'attribution':'association_not_goal_progress'}
    def candidates(self,state,allowed,blocked):
        group=state.get('interface_layout_group') or {}
        if not group or group.get('options'):return []
        candidates=[]
        for key in allowed:
            if [key] in blocked:continue
            samples=self.samples(group['id'],key);success=[s for s in samples if s['changed']]
            current_failures=sum(s['origin']==state['text_key'] and not s['changed'] for s in samples[-8:])
            if len(samples)<3 or len(success)<2 or len({s['origin'] for s in success})<2 or len(success)/len(samples)<.5 or current_failures>=2:continue
            candidates.append({'keys':[key],'samples':len(samples),'response_count':len(success),'response_rate':len(success)/len(samples),
                'source_text_states':len({s['origin'] for s in success}),'layout':group['id'],'current_text_failures':current_failures,'status':'unverified_layout_transfer','goal_success_verified':False})
        return sorted(candidates,key=lambda r:(r['response_rate'],r['samples']),reverse=True)
