"""Separate geometric frame candidates from input-blocking interface evidence."""
def allows_field_input(state):
    observation=state.get('input_surface_observation') or {}
    passive=bool(state.get('frame_sha256')) and observation.get('kind')=='passive_message' and observation.get('frame_sha256')==state.get('frame_sha256') and bool(observation.get('evidence'))
    return passive or not(state.get('interface_hypothesis') or state.get('ui_visual',{}).get('active'))

def corroborated_frame(frames,native,size):
    if frames.get('visible') is not True:return False
    if frames.get('color_basis')!='bright_saturated_border':return True
    # Colored sprite outlines are frame candidates, not menu facts. Keep the
    # geometry for diagnostics; require independently located text to activate UI.
    width,height=size
    for line in native.get('lines',[]):
        if sum(c.isalpha() for c in line.get('text',''))<2:continue
        boxes=[w['box'] for w in line.get('words',[]) if len(w.get('box',[]))==4]
        if not boxes:continue
        cx=(min(b[0] for b in boxes)+max(b[0]+b[2] for b in boxes))/2/width
        cy=(min(b[1] for b in boxes)+max(b[1]+b[3] for b in boxes))/2/height
        if any(r[0]<=cx<=r[2] and r[1]<=cy<=r[3] for r in frames.get('rectangles',[])):return True
    return False
