"""Bounded text with independent footer/control evidence; no guessed keycaps."""
import re

def collect(native,size):
    lines=native.get('lines',[]);quotes=[str(l.get('text',''))[:180] for l in lines][:160]
    footer=[];height=size[1]
    for line in lines:
        boxes=[w['box'] for w in line.get('words',[]) if len(w.get('box',[]))==4]
        if boxes and min(b[1] for b in boxes)>=height*.84:
            text=str(line.get('text',''))[:180]
            if text and text not in footer:footer.append(text)
    # Preserve punctuation-only editable content separately from layout grouping.
    masked=[re.sub(r'\s+','',q) for q in quotes if re.fullmatch(r'[\s*＊•●·_＿]{3,}',q) and any(c in '*＊•●·_＿' for c in q)]
    return quotes,footer,masked

def excerpt(state,limit=20):
    footer=state.get('footer_quotes',[])[:12];selected=(state.get('selection_observation') or {}).get('quote')
    tail=([selected] if selected else [])+footer
    out=[]
    for value in state.get('quotes',[])[:max(0,limit-len(tail))]+tail:
        if value not in out:out.append(value)
    return out[:limit]
