"""Measured glyph lattices, without guessed characters or fixed UI coordinates."""
from statistics import median
from PIL import ImageChops,ImageFilter
from .state_hash import digest


def glyph_grid(image,frame):
    from .ui_vision import components,center
    gray=image.convert('L');contrast=ImageChops.difference(gray,gray.filter(ImageFilter.MedianFilter(9)))
    hist=contrast.histogram();total=sum(hist);acc=0;quantile=0
    for i,n in enumerate(hist):
        acc+=n
        if acc>=total*.9:quantile=i;break
    threshold=max(12,quantile*.5)
    mask=contrast.point(lambda p:255 if p>threshold else 0).filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
    w,h=image.size
    boxes=[b for b in components(mask,512) if 2<= (b[2]-b[0])*w<=w*.08 and 2<=(b[3]-b[1])*h<=h*.09]
    if len(boxes)<12:return {'status':'unknown','cells':[]}
    height=median((b[3]-b[1])*h for b in boxes)
    boxes=[b for b in boxes if height*.55<=(b[3]-b[1])*h<=height*1.6 and height*.2<=(b[2]-b[0])*w<=height*1.8]
    def group(boxes,axis,tolerance):
        groups=[]
        for b in sorted(boxes,key=lambda b:center(b)[axis]):
            v=center(b)[axis]
            if not groups or abs(v-median(center(x)[axis] for x in groups[-1]))>tolerance:groups.append([b])
            else:groups[-1].append(b)
        return groups
    rows=[g for g in group(boxes,1,height*.55/h) if len(g)>=4]
    if len(rows)<3:return {'status':'unknown','cells':[]}
    gaps=[median(center(b)[1] for b in rows[i+1])-median(center(b)[1] for b in rows[i]) for i in range(len(rows)-1)]
    pitch=median(gaps);runs=[[]]
    for i,row in enumerate(rows):
        if i and not pitch*.65<=gaps[i-1]<=pitch*1.4:runs.append([])
        runs[-1].append(row)
    rows=max(runs,key=lambda run:sum(len(g) for g in run))
    if len(rows)<3:return {'status':'unknown','cells':[]}
    width=median((b[2]-b[0])*w for row in rows for b in row)
    cols=[g for g in group([b for row in rows for b in row],0,max(width*.65,2)/w) if len(g)>=3]
    if len(cols)<3:return {'status':'unknown','cells':[]}
    cells=[]
    for ri,row in enumerate(rows):
        for ci,col in enumerate(cols):
            matches=[b for b in row if b in col]
            if len(matches)!=1:continue
            b=matches[0];cells.append({'id':'pixel_cell:'+digest([round(center(b)[0]*100),round(center(b)[1]*100)]),'bbox':b,'center':center(b),'text':None,'row':ri,'column':ci,'kind':'visual_glyph_cell_hypothesis','source_frames':[frame]})
    occupancy=len(cells)/(len(rows)*len(cols))
    if occupancy<.55:return {'status':'unknown','cells':[]}
    return {'status':'hypothesis','rows':len(rows),'columns':len(cols),'occupancy':occupancy,'confidence':.4,'coordinate_source':'measured_contrast_components_not_ocr','cells':cells[:128]}


def selection_elements(texts,cells):
    """Don't assign a tall OCR column's complete string to one grid character."""
    from .ui_vision import overlap
    if not cells:return texts
    height=median(b['bbox'][3]-b['bbox'][1] for b in cells)
    compact=[e for e in texts if e['bbox'][3]-e['bbox'][1]<=height*2.5]
    out=list(compact)
    for cell in cells:
        if not any(overlap(cell['bbox'],e['bbox'])>.5 and e['bbox'][2]-e['bbox'][0]< (cell['bbox'][2]-cell['bbox'][0])*2.5 for e in compact):out.append(cell)
    return out
