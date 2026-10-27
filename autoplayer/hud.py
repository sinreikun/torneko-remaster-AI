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


# Exact labels only. No semantic guess from an isolated number or old frame.
STATUS_LABELS={'HP':'hp','ちから':'strength','力':'strength','剣の強さ':'sword','盾の強さ':'shield',
    '最深階':'max_floor','経験値':'experience','満腹度':'fullness','レベル':'level','所持金':'gold'}


def boxed_statuses(native):
    """Pair separate OCR boxes by row and column boundaries in this frame."""
    import math
    if native.get('status')!='ok':return {}
    atoms={}
    def valid_box(box):
        return isinstance(box,(list,tuple)) and len(box)==4 and all(isinstance(v,(int,float)) and math.isfinite(v) for v in box) and min(box[2:])>0
    for line in native.get('lines',[]):
        confidence=line.get('confidence')
        if isinstance(confidence,(int,float)) and confidence<.6:continue
        words=[w for w in line.get('words',[]) if valid_box(w.get('box'))]
        parts=[(w.get('text',''),w['box']) for w in words]
        if words:
            x=min(w['box'][0] for w in words);y=min(w['box'][1] for w in words)
            parts.append((line.get('text',''),[x,y,max(w['box'][0]+w['box'][2] for w in words)-x,max(w['box'][1]+w['box'][3] for w in words)-y]))
        for raw,box in parts:
            if any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in box) or min(box[2:])<=0:continue
            text=compact_text(raw)
            atoms[text,tuple(box)]=(text,box,raw,confidence)
    labels=[a for a in atoms.values() if a[0] in STATUS_LABELS];found={}
    barriers=[a for a in atoms.values() if re.search(r'[A-Za-zぁ-んァ-ヶ一-龯]',a[0]) and not re.fullmatch(r'\d+(?:G|階)',a[0],re.I)]
    def same_row(a,b):
        overlap=min(a[1]+a[3],b[1]+b[3])-max(a[1],b[1])
        return overlap>=min(a[3],b[3])*.65
    for label,box,quote,confidence in labels:
        name=STATUS_LABELS[label];right=box[0]+box[2]
        boundary=min((other[1][0] for other in barriers if other[1][0]>=right and same_row(box,other[1])),default=float('inf'))
        candidates=[]
        for text,value_box,raw,value_confidence in atoms.values():
            if not same_row(box,value_box) or not right<=value_box[0]<boundary:continue
            if value_box[0]+value_box[2]>boundary or value_box[0]-right>max(200,box[3]*12):continue
            pattern=(r'(\d{1,4})/(\d{1,4})' if name in ('hp','strength') else r'(\d{1,3})%' if name=='fullness' else r'([+-]?\d{1,4})' if name in ('sword','shield') else r'(\d{1,9})G?' if name=='gold' else r'(\d{1,9})(?:階)?')
            match=re.fullmatch(pattern,text,re.I)
            if not match:continue
            values=[int(v) for v in match.groups()]
            if name in ('hp','strength') and (values[1]<=0 or values[0]>values[1]):continue
            if name=='fullness' and values[0]>100:continue
            if name in ('level','max_floor') and not 1<=values[0]<=999:continue
            candidates.append((values,raw,value_box,value_confidence))
        # Multiple numbers to the right are ambiguous, even if they happen to
        # have the same value. Never choose by nearest distance alone.
        if len(candidates)!=1:continue
        values,raw,value_box,value_confidence=candidates[0]
        found.setdefault(name,[]).append({'values':values,'quote':quote+' / '+raw,'evidence_quotes':[quote,raw],
            'source':'same_row_boxed_label_value','frame_sha256':native.get('frame_sha256'),
            'label_box':box,'value_box':value_box,'unverified_ocr':True})
    return {name:readings[0] for name,readings in found.items() if len({tuple(r['values']) for r in readings})==1}
