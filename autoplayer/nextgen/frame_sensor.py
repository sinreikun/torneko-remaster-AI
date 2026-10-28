"""Measure bright rectangular interface borders independently of hue."""
import io
from functools import lru_cache
from PIL import Image,ImageChops
from ..exterior_screen import white_frames

@lru_cache(maxsize=8)
def interface_frames(png):
    ordinary=white_frames(png)
    if ordinary.get('visible') is True:return {**ordinary,'color_basis':'neutral_border'}
    try:
        with Image.open(io.BytesIO(png)) as source:image=source.convert('RGB')
        r,g,b=image.split();high=ImageChops.lighter(r,ImageChops.lighter(g,b));low=ImageChops.darker(r,ImageChops.darker(g,b))
        bright=high.point(lambda v:255 if v>=170 else 0)
        saturated=ImageChops.subtract(high,low).point(lambda v:255 if v>=60 else 0)
        contrast=low.point(lambda v:255 if v<=135 else 0)
        accent=ImageChops.multiply(bright,ImageChops.multiply(saturated,contrast))
        if not accent.getbbox():return ordinary
        # Only a measurement mask: OCR and the LLM still receive original pixels.
        measured=Image.composite(Image.new('RGB',image.size,'white'),image,accent)
        out=io.BytesIO();measured.save(out,format='PNG',compress_level=1)
        result=white_frames(out.getvalue())
        if result.get('visible') is True:return {**result,'color_basis':'bright_saturated_border','semantic_status':'interface_geometry_hypothesis'}
        return ordinary
    except (OSError,ValueError):return ordinary
