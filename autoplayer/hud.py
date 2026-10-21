"""Join an independently boxed HUD label/value on the same visual row only."""
import re
from .interfaces import compact_text

def boxed_fullness(native):
    labels=[];values=[]
    for line in native.get('lines',[]):
        boxes=[w['box'] for w in line.get('words',[]) if len(w.get('box',[]))==4]
        if not boxes:continue
        x=min(b[0] for b in boxes);y=min(b[1] for b in boxes)
        right=max(b[0]+b[2] for b in boxes);bottom=max(b[1]+b[3] for b in boxes)
        text=compact_text(line.get('text',''))
        if text=='満腹度':labels.append((x,y,right,bottom))
        match=re.fullmatch(r'(\d{1,3})%',text)
        if match and int(match[1])<=100:values.append(((x,y,right,bottom),int(match[1]),line['text']))
    found=[]
    for label in labels:
        candidates=[]
        for box,value,quote in values:
            h=min(label[3]-label[1],box[3]-box[1])
            overlap=min(label[3],box[3])-max(label[1],box[1])
            if h>0 and overlap>=h*.65 and 0<=box[0]-label[2]<=h*4:candidates.append((value,quote))
        if len(candidates)==1:found.append(candidates[0])
    if len(found)==1:return {'values':[found[0][0]],'quote':'満腹度 / '+found[0][1],
        'source':'same_row_boxed_label_value','unverified_ocr':True}
    return None
