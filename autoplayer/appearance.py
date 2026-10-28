"""Bounded anonymous appearance exemplars; matching is a hypothesis, not a name."""
import json
import math
import uuid


def descriptor(image):
    # Color and silhouette layout, with mild blur to tolerate animation pixels.
    from PIL import ImageFilter
    patch=image.convert('RGB').resize((8,8)).filter(ImageFilter.GaussianBlur(.6))
    return [round(v/255,3) for pixel in patch.getdata() for v in pixel]


class AppearanceBank:
    def __init__(self,db=None,profile='session'):
        self.db=db;self.profile=profile;self.samples=[]
        if db:
            db.execute('CREATE TABLE IF NOT EXISTS appearance_samples(profile TEXT,class_id TEXT,pose TEXT,descriptor TEXT)')
            self.samples=[(a,b,json.loads(c)) for a,b,c in db.execute('SELECT class_id,pose,descriptor FROM appearance_samples WHERE profile=? ORDER BY rowid DESC LIMIT 512',(profile,))]
    def observe(self,image,class_id=None,pose='unknown'):
        vector=descriptor(image);scores={}
        for label,_,sample in self.samples:
            distance=math.sqrt(sum((a-b)**2 for a,b in zip(vector,sample))/len(vector))
            scores[label]=min(scores.get(label,1),distance)
        ranked=sorted(scores.items(),key=lambda p:p[1]);match=None
        if ranked and ranked[0][1]<.085 and (len(ranked)<2 or ranked[1][1]-ranked[0][1]>.035):match=ranked[0][0]
        # Only a continuous tracked identity teaches variants. Similarity never
        # merges tracks or teaches itself; ambiguous candidates stay hypotheses.
        if class_id is None:class_id='appearance_'+uuid.uuid4().hex[:16]
        own=[d for c,_,d in self.samples if c==class_id]
        if len(own)<32 and not any(sum(abs(a-b) for a,b in zip(vector,d))/len(vector)<.008 for d in own):
            self.samples.append((class_id,pose,vector));self.samples=self.samples[-512:]
            if self.db:
                self.db.execute('INSERT INTO appearance_samples VALUES(?,?,?,?)',(self.profile,class_id,pose,json.dumps(vector)))
                self.db.execute('DELETE FROM appearance_samples WHERE profile=? AND rowid NOT IN (SELECT rowid FROM appearance_samples WHERE profile=? ORDER BY rowid DESC LIMIT 512)',(self.profile,self.profile));self.db.commit()
        return {'class_id':class_id,'similar_class_candidate':match,'status':'appearance_hypothesis','sample_count':sum(c==class_id for c,_,_ in self.samples),'pose':pose,'identity_verified':False}
