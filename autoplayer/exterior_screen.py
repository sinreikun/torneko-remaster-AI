"""Image measurements for the user's explicit exterior movement assumption."""
import io
from PIL import Image, ImageChops, ImageFilter


def flat_pixels(image):
    return image.get_flattened_data() if hasattr(image,'get_flattened_data') else image.getdata()

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
        white=white.filter(ImageFilter.MaxFilter(kernel))
    mask=[v>=64 for v in flat_pixels(white.resize((width,height),Image.Resampling.BOX))]
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
        start=None;last=None
        for x in range(3,width-3):
            if mask[y*width+x]:
                if start is None:start=x
                last=x
            elif start is not None and x-last>2:
                if last-start>=minimum:add_line(y,start,last)
                start=last=None
        if start is not None and last-start>=minimum:add_line(y,start,last)
        if len(horizontal)>192:return {'status':'ambiguous','visible':None,'rectangles':[]}
    rectangles=[]
    for index,(top,left,right) in enumerate(horizontal):
        for bottom,l,r in horizontal[index+1:]:
            if bottom-top<12 or abs(l-left)>3 or abs(r-right)>3:continue
            if bottom-top>height*.88:break
            def edge(x):
                return sum(any(mask[y*width+u] for u in range(max(0,x-2),min(width,x+3))) for y in range(top,bottom+1))/(bottom-top+1)
            if min(edge(left),edge(right))<.75:continue
            interior=[pixels[y*width+x] for y in range(top+3,bottom-2,max(3,(bottom-top)//12)) for x in range(left+3,right-2,max(3,(right-left)//20))]
            if not interior or sum(max(p)<145 for p in interior)/len(interior)<.4:continue
            rectangle=[round(left/width,4),round(top/height,4),round(right/width,4),round(bottom/height,4)]
            if not any(abs(rectangle[0]-a[0])<.012 and abs(rectangle[1]-a[1])<.012 for a in rectangles):rectangles.append(rectangle)
            if len(rectangles)>=8:return {'status':'observed','visible':True,'rectangles':rectangles,'basis':'white_rectangle_dark_interior'}
    return {'status':'observed','visible':bool(rectangles),'rectangles':rectangles,'basis':'white_rectangle_dark_interior'}


def assumed_change(before,after,neutral):
    gates=[white_frames(p) for p in (before,after)]
    if any(g['visible'] is None for g in gates):return {'status':'unknown','reason':'unreadable_image','white_frames':gates}
    if any(g['visible'] for g in gates):return {'status':'interface','reason':'white_frame_before_or_after_input','white_frames':gates}
    def image(png):return Image.open(io.BytesIO(png)).convert('RGB').resize((160,90)).crop((0,12,160,85))
    a,b=image(before),image(after)
    def fraction(other):
        return sum(max(p)>=22 for p in flat_pixels(ImageChops.difference(a,other)))/(160*73)
    changed=fraction(b)
    try:natural=fraction(image(neutral))
    except Exception:return {'status':'unknown','reason':'unreadable_neutral_image','white_frames':gates}
    threshold=max(.035,natural+.02)
    return {'status':'assumed_moved' if changed>=threshold else 'assumed_blocked','changed_fraction':round(changed,5),
        'natural_fraction':round(natural,5),'large_change_threshold':round(threshold,5),'white_frames':gates,
        'basis':'user_assumption_screen_change_not_measured_displacement'}
