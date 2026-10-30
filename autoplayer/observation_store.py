"""Per-turn exact-frame observations and bounded action-message evidence."""
import copy,hashlib,io
from collections import OrderedDict

class ObservationStore:
    def __init__(self):self.turn=None;self.cache=OrderedDict();self.hits=0;self.misses=0;self.bypasses=0
    def begin(self,turn):
        if turn!=self.turn:self.turn=turn;self.cache.clear();self.hits=self.misses=self.bypasses=0
    def read(self,png,mode,producer,independent=False):
        digest=hashlib.sha256(png).hexdigest();key=(digest,mode)
        if independent:self.bypasses+=1
        elif key in self.cache:
            self.hits+=1;return copy.deepcopy(self.cache[key])
        self.misses+=1;value=producer()
        if not independent and value and value.get('status')=='ok' and value.get('frame_sha256')==digest:
            self.cache[key]=copy.deepcopy(value)
            while len(self.cache)>12:self.cache.popitem(last=False)
        return value
    def context(self):return {'turn':self.turn,'hits':self.hits,'reads':self.misses,'independent_reads':self.bypasses,'entries':len(self.cache),'basis':'exact_bytes_mode_turn_only'}

def message_candidates(final,frames,limit=2):
    """Rank old frames only for message OCR; never substitute current metrics."""
    from PIL import Image,ImageChops,ImageStat
    seen={hashlib.sha256(final).digest()};ranked=[]
    try:
        with Image.open(io.BytesIO(final)) as im:
            ref=im.convert('L');ref=ref.crop((0,int(ref.height*.55),ref.width,int(ref.height*.98))).resize((128,48))
    except (OSError,ValueError):return []
    for index,png in enumerate(frames):
        digest=hashlib.sha256(png).digest()
        if digest in seen:continue
        seen.add(digest)
        try:
            with Image.open(io.BytesIO(png)) as im:
                band=im.convert('L');band=band.crop((0,int(band.height*.55),band.width,int(band.height*.98))).resize((128,48))
            change=ImageStat.Stat(ImageChops.difference(ref,band)).mean[0]
            if change>1:ranked.append((change,index,png))
        except (OSError,ValueError):continue
    return [p for _,_,p in sorted(ranked,key=lambda r:(r[0],r[1]),reverse=True)[:limit]]
