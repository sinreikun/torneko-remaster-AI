"""Torneko observation adapter. Reuses observers, never the legacy Controller."""
import copy,re
from ..world_model import Inventory
from ..interfaces import torneko_menu_kind,compact_text,normalize_item_menu,ordinary_choice,result_screen,PROTECTED_CONFIRMATION
from ..equipment_preparation import EquipmentPreparation
from .state_hash import digest

class DungeonObserver:
    def __init__(self,scope,descriptions=()):
        self.inventory=Inventory(scope);self.inventory.description_reads=copy.deepcopy(list(descriptions)[-40:]);self.last_frame=None;self.last=None
    def reset(self,scope):self.__init__(scope,[r for r in self.inventory.description_reads if r.get('identity_scope')=='profile_display_name'])
    def observe(self,state,native,png=None):
        if native.get('frame_sha256')!=state['frame_sha256'] or native.get('status')!='ok':return {'status':'unavailable','frame':state['frame_sha256']}
        # OCR can be refreshed on identical pixels; do not freeze an unknown cursor.
        native=normalize_item_menu(native);kind=torneko_menu_kind(native);inv=self.inventory.observe(native,png)
        lines=native.get('lines',[]);index=None
        selected=state.get('selection_observation',{})
        if selected.get('status')=='supported_association' and selected.get('frame_sha256')==state['frame_sha256']:index=selected.get('line_index')
        if index is None:index=Inventory.cursor_index(native,png)
        entries=[{'line':i,'text':compact_text(l.get('text','')).lstrip('▶►▷>●→'),'box':Inventory.line_box(l)} for i,l in enumerate(lines)]
        if kind=='inventory' and inv.get('status')=='observed':
            signature=digest([(r['display_name'],r.get('quantity')) for r in inv['rows']])
            for row in inv['rows']:
                row['entry_id']=digest([signature,row.get('line_index'),row['display_name']]);row['page_signature']=signature
                row['selected']=row.get('line_index')==index
                option=EquipmentPreparation.option(row)
                if option:row['equipment_estimate']={'slot':option[0],'value':option[1],'special':option[2],'source':'description_and_display_modifier_not_equipped_status'}
            inv['selected_id']=next((r['entry_id'] for r in inv['rows'] if r['selected']),None)
            inv['page_signature']=signature;inv['frame_sha256']=state['frame_sha256']
            state['inventory_observation']=copy.deepcopy(inv)
        panel=inv.get('description_panel',{})
        row=next((r for r in inv.get('rows',[]) if r.get('selected')),None)
        if row and panel.get('status')=='located' and panel.get('quotes'):
            record={'status':'description_text_observed','item_name':row['display_name'],'item_id':row['id'],'inventory_scope':self.inventory.scope,'identity_scope':'inventory_alias' if row.get('identification_required') else 'profile_display_name','quotes':panel['quotes'],'source':'current_selected_inventory_panel','source_frame_sha256':state['frame_sha256'],'effect_verified':False}
            if not any(r.get('item_name')==record['item_name'] and r.get('quotes')==record['quotes'] and r.get('item_id')==record['item_id'] for r in self.inventory.description_reads):self.inventory.description_reads=(self.inventory.description_reads+[record])[-40:]
            row['known_description']=record
            option=EquipmentPreparation.option(row)
            if option:row['equipment_estimate']={'slot':option[0],'value':option[1],'special':option[2],'source':'description_and_display_modifier_not_equipped_status'}
        if inv.get('status')=='observed':
            equipped_slots={r['equipment_estimate']['slot']:r['entry_id'] for r in inv['rows'] if r.get('equipped') is True and r.get('equipment_estimate')}
            for r in inv['rows']:
                slot=r.get('equipment_estimate',{}).get('slot')
                if slot in equipped_slots and r['entry_id']!=equipped_slots[slot]:
                    r['equipped']=False;r['equipment_evidence']='other_observed_equipped_entry_in_single_slot'
            state['inventory_observation']=copy.deepcopy(inv)
        if ordinary_choice(native):kind='choice'
        elif result_screen(native):kind='result'
        view={'protected':bool(PROTECTED_CONFIRMATION.search(' '.join(l.get('text','') for l in lines))),'status':'observed','kind':kind,'entries':entries,'selected_line':index,'selection_source':'supported_ui_association' if selected.get('status')=='supported_association' else 'current_cursor_candidate','inventory':inv,'frame':state['frame_sha256']}
        self.last_frame=state['frame_sha256'];self.last=copy.deepcopy(view);return view
