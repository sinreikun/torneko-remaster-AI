"""Locate description text by current frame geometry, never by a fixed side."""
import re
from .interfaces import compact_text

def line_box(line):
    boxes=[w['box'] for w in line.get('words',[]) if len(w.get('box',[]))==4]
    if not boxes:return None
    return [min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[0]+b[2] for b in boxes),max(b[1]+b[3] for b in boxes)]

def locate_description(native,png,selected_index,known_labels=()):
    result={'status':'unmeasured','rectangles':[],'line_indices':[],'quotes':[]}
    if not png or not png.startswith(b'\x89PNG\r\n\x1a\n') or len(png)<24:return result
    lines=(native or {}).get('lines',[])
    if selected_index is None or not 0<=selected_index<len(lines):return result
    selected=line_box(lines[selected_index])
    if not selected:return result
    from .exterior_screen import white_frames
    frames=white_frames(png)
    if frames.get('status')!='observed':return result
    width=int.from_bytes(png[16:20],'big');height=int.from_bytes(png[20:24],'big')
    if not width or not height:return result
    clean=lambda t:compact_text(t).lstrip('▶►▷>●→')
    names={clean(t) for t in known_labels}|{clean(lines[selected_index].get('text',''))}
    candidates=[]
    for rect in frames.get('rectangles',[]):
        left,top,right,bottom=[v*(width if i%2==0 else height) for i,v in enumerate(rect)]
        # The inventory's outer frame contains the cursor. It is not the
        # explanation popup, regardless of which side the popup occupies.
        sx=(selected[0]+selected[2])/2;sy=(selected[1]+selected[3])/2
        if left<=sx<=right and top<=sy<=bottom:continue
        indices=[];quotes=[]
        for index,line in enumerate(lines):
            text=line.get('text','');label=clean(text);box=line_box(line)
            if not box or index==selected_index or label in names or len(label)<5:continue
            if re.search(r'^(?:HP\d|Lv\d|\d+階$|\d+/\d+$|\d+G$)',label,re.I) or label in ('詳細表示切替','満腹度','剣の強さ','盾の強さ','所持アイテム'):continue
            cx=(box[0]+box[2])/2;cy=(box[1]+box[3])/2
            if left+1<cx<right-1 and top+1<cy<bottom-1:
                indices.append(index);quotes.append(text[:800])
        if quotes:candidates.append({'rectangle':rect,'line_indices':indices,'quotes':quotes[:12]})
    # Duplicate/nested outline detections with identical text are one region.
    groups={tuple(c['line_indices']):c for c in sorted(candidates,key=lambda c:-(c['rectangle'][2]-c['rectangle'][0])*(c['rectangle'][3]-c['rectangle'][1]))}
    if len(groups)!=1:return {**result,'status':'ambiguous' if groups else 'not_detected'}
    candidate=next(iter(groups.values()))
    return {'status':'located','rectangles':[candidate['rectangle']],'line_indices':candidate['line_indices'],'quotes':candidate['quotes'],'basis':'current_outline_and_ocr_excluding_selected_list_frame'}
