"""Stable interface-layout hypotheses; OCR noise is not a new world state."""
from collections import OrderedDict
import re
from .state_hash import digest

def normalized(text):
    return re.sub(r'[\W\d_]+','',text.casefold(),flags=re.UNICODE)

class InterfaceIdentity:
    def __init__(self):self.layouts=OrderedDict()
    def observe(self,native,selection=None):
        tokens={normalized(line.get('text','')) for line in native.get('lines',[])}
        tokens={t for t in tokens if len(t)>=2}
        best=None
        for key,known in self.layouts.items():
            score=len(tokens&known)/max(1,len(tokens|known))
            if score>=.6 and (best is None or score>best[0]):best=(score,key)
        key=best[1] if best else digest(['interface_layout_v1',sorted(tokens)])
        if not best:self.layouts[key]=tokens
        self.layouts.move_to_end(key)
        while len(self.layouts)>128:self.layouts.popitem(last=False)
        selected=None
        lines=native.get('lines',[])
        if selection is not None and selection<len(lines):
            line=lines[selection];boxes=[w['box'] for w in line.get('words',[]) if len(w.get('box',[]))==4]
            selected=[normalized(line.get('text','')),round(min(b[0] for b in boxes)/32) if boxes else None,round(min(b[1] for b in boxes)/32) if boxes else None]
        return key,selected
