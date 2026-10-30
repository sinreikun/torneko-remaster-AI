"""RapidOCR 3.4 recognizer adapter: exact cropped-pixel reuse, not text guessing."""
import hashlib,time
from collections import OrderedDict
from dataclasses import replace,is_dataclass

class CroppedRecognitionCache:
    def __init__(self,recognizer,limit=256):
        self.recognizer=recognizer;self.limit=limit;self.enabled=False;self.force_fresh=False;self.cache=OrderedDict();self.template=None
        self.hits=0;self.misses=0;self.last_records=[]
    def __getattr__(self,name):return getattr(self.recognizer,name)
    def __call__(self,args):
        self.hits=0;self.misses=0;self.last_records=[]
        images=[args.img] if hasattr(args.img,'shape') else list(args.img)
        if not self.enabled or args.return_word_box or not is_dataclass(args):
            result=self.recognizer(args);self.misses=len(images)
            self.last_records=[(t,s,time.time()) for t,s in zip(result.txts or [],result.scores)]
            return result
        keys=[(tuple(im.shape),str(im.dtype),hashlib.sha256(im.tobytes()).hexdigest()) for im in images]
        missing=[i for i,key in enumerate(keys) if self.force_fresh or key not in self.cache]
        if missing:
            result=self.recognizer(replace(args,img=[images[i] for i in missing]))
            if not is_dataclass(result) or len(result.txts or [])!=len(missing):return self.recognizer(args)
            self.template=replace(result,imgs=None);stamp=time.time()
            for i,text,score in zip(missing,result.txts,result.scores):self.cache[keys[i]]=(text,score,stamp)
        if self.template is None:return self.recognizer(args)
        records=[self.cache[key] for key in keys]
        for key in keys:self.cache.move_to_end(key)
        while len(self.cache)>self.limit:self.cache.popitem(last=False)
        self.hits=len(images)-len(missing);self.misses=len(missing);self.last_records=records
        return replace(self.template,imgs=images,txts=tuple(r[0] for r in records),scores=tuple(r[1] for r in records),word_results=[],elapse=self.template.elapse if missing else 0.)
