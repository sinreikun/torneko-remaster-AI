"""Observed list-entry identity and text-color evidence; no inferred true names."""
import io

def review_key(row):
    if row.get('id'):return ('entry',tuple(row.get('page') or ()),row['id'])
    box=row.get('box') or (0,0,0,0)
    return ('row',tuple(row.get('page') or ()),row.get('line_index'),round((box[1]+box[3])/2/16))

def same_label(a,b):
    if a==b:return True
    # An equipment E can be merged into the same row's OCR as 巨. This is
    # only an OCR-label association, never evidence that the item is equipped.
    return any(a==prefix+b or b==prefix+a for prefix in ('E','Ｅ','巨'))

def orange_text(png,box):
    if not png or not box:return {'status':'unobserved'}
    try:
        from PIL import Image
        source=row_image(png)
        if source:
            x0,y0,x1,y1=map(round,box);im=source.crop((max(0,x0),max(0,y0),min(source.width,x1),min(source.height,y1)))
            bright=orange=0
            for r,g,b in im.getdata():
                if max(r,g,b)<110:continue
                bright+=1
                orange+=r>=150 and 65<=g<=220 and b<min(150,g*.85) and r>=g*1.12
        observed=orange>=max(8,im.height*.6) and orange/max(1,bright)>=.35
        return {'status':'orange_unidentified' if observed else 'orange_not_confirmed','orange_pixels':orange,'bright_pixels':bright,'source':'current_inventory_row_pixels'}
    except (OSError,ValueError):return {'status':'unobserved'}

def equipped_indicator(png,box):
    """Recognize the small white E glyph, distinct from the full-size cursor."""
    if not png or not box:return False
    try:
        from .minimap import components
        im=row_image(png);pixels=im.load();x0,y0,x1,y1=box;h=y1-y0
        left=max(0,round(x0-h));right=min(im.width,round(x0+h*.7))
        top=max(0,round(y0));bottom=min(im.height,round(y1))
        points={(x,y) for y in range(top,bottom) for x in range(left,right) if min(pixels[x,y])>185 and max(pixels[x,y])-min(pixels[x,y])<45}
        for part in components(points):
            a=min(x for x,y in part);b=min(y for x,y in part);w=max(x for x,y in part)-a+1;gh=max(y for x,y in part)-b+1
            if not 6<=gh<=h*.65 or not .6<=w/gh<=1.3:continue
            stem=sum(any((x,y) in part for x in range(a,a+max(1,w//4))) for y in range(b,b+gh))/gh
            bars=[max(sum((x,y) in part for x in range(a,a+w))/w for y in range(b+int(gh*lo),min(b+gh,b+int(gh*hi)+1))) for lo,hi in ((0,.2),(.35,.65),(.8,.95))]
            if stem>=.85 and min(bars)>=.75:return True
        return False
    except (OSError,ValueError):return False

from functools import lru_cache
@lru_cache(maxsize=2)
def row_image(png):
    from PIL import Image
    with Image.open(io.BytesIO(png)) as image:return image.convert('RGB')
