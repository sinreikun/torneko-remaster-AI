"""Pixel-equivalent image primitives; no observation thresholds are changed."""
from PIL import ImageChops,ImageFilter

def maximum3(image):
    """Separable 3x3 maximum, identical to MaxFilter(3) for L masks.

    Zeroing wrapped pixels is safe for nonnegative masks: the original edge
    pixel remains in the maximum, matching replicated boundary behavior.
    """
    if image.mode!='L':return image.filter(ImageFilter.MaxFilter(3))
    w,h=image.size
    left=ImageChops.offset(image,-1,0);left.paste(0,(w-1,0,w,h))
    right=ImageChops.offset(image,1,0);right.paste(0,(0,0,1,h))
    horizontal=ImageChops.lighter(image,ImageChops.lighter(left,right))
    up=ImageChops.offset(horizontal,0,-1);up.paste(0,(0,h-1,w,h))
    down=ImageChops.offset(horizontal,0,1);down.paste(0,(0,0,w,1))
    return ImageChops.lighter(horizontal,ImageChops.lighter(up,down))
