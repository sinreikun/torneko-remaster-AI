"""Image measurements for the user's explicit exterior movement assumption."""
import io
import re
from functools import lru_cache
from PIL import Image, ImageChops, ImageFilter


def flat_pixels(image):
    return image.get_flattened_data() if hasattr(image,'get_flattened_data') else image.getdata()

@lru_cache(maxsize=6)
def white_frames(png):
    """Find near-white rectangular outlines with dark interiors, not text alone."""
    try:
        source=Image.open(io.BytesIO(png)).convert('RGB')
        width=min(640,source.width);height=max(1,round(source.height*width/source.width))
        im=source.resize((width,height));pixels=list(flat_pixels(im))
    except Exception:return {'status':'unreadable','visible':None,'rectangles':[]}
    channels=source.split()
    low=ImageChops.darker(channels[0],ImageChops.darker(channels[1],channels[2]))
    high=ImageChops.lighter(channels[0],ImageChops.lighter(channels[1],channels[2]))
    white=ImageChops.multiply(low.point(lambda v:255 if v>=185 else 0),
        ImageChops.subtract(high,low).point(lambda v:255 if v<=48 else 0))
    if source.width>width:
        # Preserve 1-2px outlines before reducing the capture. RGB averaging
        # alone can erase white borders whose pixels fall between samples.
        kernel=min(9,max(3,2*int(source.width/width)//2+1))
        # A square maximum is separable across repeated radius-one maxima.
        # This preserves exactly the same pixels while avoiding a large kernel.
        for _ in range((kernel-1)//2):white=white.filter(ImageFilter.MaxFilter(3))
    mask=white.resize((width,height),Image.Resampling.BOX).point(lambda v:255 if v>=64 else 0).tobytes()
    horizontal=[];minimum=max(36,int(width*.065))
    def add_line(y,left,right):
        # Broad bright surfaces (beds, blank captures) are not frame edges.
        # Demand dark pixels on one adjacent side before pairing any lines.
        for side in (y-4,y+4):
            if 0<=side<height:
                samples=[pixels[side*width+x] for x in range(left+3,right-2,4)]
                if samples and sum(max(p)<145 for p in samples)/len(samples)>=.4:
                    if not horizontal or not (y-horizontal[-1][0]<=2 and abs(left-horizontal[-1][1])<=2 and abs(right-horizontal[-1][2])<=2):horizontal.append((y,left,right))
                    return

    for y in range(3,height-3):
        # Find exactly the same near-contiguous white runs in C rather than
        # visiting every screenshot pixel in the Python interpreter.
        row=mask[y*width+3:y*width+width-3]
        for run in re.finditer(b'\xff(?:\x00{0,2}\xff)*',row):
            left=run.start()+3;right=run.end()+2
            if right-left>=minimum:add_line(y,left,right)
        if len(horizontal)>192:return {'status':'ambiguous','visible':None,'rectangles':[]}
    rectangles=[]
    for index,(top,left,right) in enumerate(horizontal):
        for bottom,l,r in horizontal[index+1:]:
            if bottom-top<12 or abs(l-left)>3 or abs(r-right)>3:continue
            if bottom-top>height*.88:break
            if (right-left)*(bottom-top)<width*height*.01:continue
            def edge(x):
                return sum(any(mask[y*width+u] for u in range(max(0,x-2),min(width,x+3))) for y in range(top,bottom+1))/(bottom-top+1)
            if min(edge(left),edge(right))<.75:continue
            interior=[pixels[y*width+x] for y in range(top+3,bottom-2,max(3,(bottom-top)//12)) for x in range(left+3,right-2,max(3,(right-left)//20))]
            if not interior or sum(max(p)<145 for p in interior)/len(interior)<.4:continue
            rectangle=[round(left/width,4),round(top/height,4),round(right/width,4),round(bottom/height,4)]
            if not any(abs(rectangle[0]-a[0])<.012 and abs(rectangle[1]-a[1])<.012 for a in rectangles):rectangles.append(rectangle)
            if len(rectangles)>=8:return {'status':'observed','visible':True,'rectangles':rectangles,'basis':'white_rectangle_dark_interior'}
    return {'status':'observed','visible':bool(rectangles),'rectangles':rectangles,'basis':'white_rectangle_dark_interior'}


def observe_change(before,after,neutral):
    """Report pixel change and frame visibility independently of input meaning."""
    gates=[white_frames(p) for p in (before,after)]
    a_visible,b_visible=(g.get('visible') for g in gates)
    transition='unknown' if None in (a_visible,b_visible) else 'appeared' if not a_visible and b_visible else 'disappeared' if a_visible and not b_visible else 'unchanged'
    result={'white_frames':gates,'frame_transition':transition}
    def image(png):return Image.open(io.BytesIO(png)).convert('RGB').resize((160,90)).crop((0,12,160,85))
    try:
        a,b=image(before),image(after)
        def fraction(other):
            return sum(max(p)>=22 for p in flat_pixels(ImageChops.difference(a,other)))/(160*73)
        changed=fraction(b);natural=fraction(image(neutral))
    except Exception:return {**result,'status':'unknown','reason':'unreadable_image_pair','large_change':None}
    threshold=max(.035,natural+.02)
    return {**result,'status':'observed','large_change':changed>=threshold,'changed_fraction':round(changed,5),
        'natural_fraction':round(natural,5),'large_change_threshold':round(threshold,5)}


def assumed_change(before,after,neutral):
    evidence=observe_change(before,after,neutral)
    if evidence['status']=='unknown':return evidence
    if any(g['visible'] is None for g in evidence['white_frames']):return {**evidence,'status':'unknown','reason':'white_frame_unavailable'}
    if any(g['visible'] for g in evidence['white_frames']):return {**evidence,'status':'interface','reason':'white_frame_before_or_after_input'}
    return {**evidence,'status':'assumed_moved' if evidence['large_change'] else 'assumed_blocked',
        'basis':'user_assumption_screen_change_not_measured_displacement'}
