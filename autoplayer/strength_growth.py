"""Priority strength growth, with a fresh status check before each consumption."""
import copy,json

SEED='ちからの種'
ANTIDOTES={'毒消し草','毒けし草'}

class StrengthGrowth:
    def __init__(self):self.active=None;self.attempted=set()
    def choose(self,actor,base,native,state,current,blocked=()):
        kind=current.get('interface');step=current['step']
        scope=(current.get('control_revision'),current.get('operator_guidance',{}).get('revision',0),tuple(current.get('floor') or ()))
        retained=state.get('retained_state',{})
        rows=native.get('_current_inventory_rows',[]) if kind=='inventory' else (retained.get('last_observed_inventory') or {}).get('rows',[])
        seed=next((r for r in rows if r.get('display_name')==SEED and not r.get('identification_required')),None)
        token=json.dumps([scope,seed.get('id') if seed else None,seed.get('quantity') if seed else None,retained.get('last_observed_metrics',{}).get('strength',{}).get('values')],ensure_ascii=False)
        a=self.active
        if a and (a['scope']!=scope or step>a['until']):self.active=None;a=None
        if kind not in (None,'main_menu','inventory','item_actions'):
            self.active=None;return None
        if not a:
            if not seed or token in self.attempted or not current.get('floor') or kind is None and not state.get('eligible'):return None
            a={'scope':scope,'until':step+32,'needs_status':True,'values':None,'target':None,'phase':'inventory','pending':None,'token':token,'pages':set()}
            self.active=a;self.attempted.add(token)
            if len(self.attempted)>128:self.attempted={token}
        def action(key,phase,reason):
            if not key or [key] in blocked:return None
            a['pending']=phase
            return actor._decision(base,[key],'strength_growth',reason)
        strength=state.get('metrics',{}).get('strength',{})
        if kind=='main_menu' and strength.get('values') and len(strength['values'])==2 and not strength.get('retained'):
            values=strength['values']
            if not 0<=values[0]<=values[1] or values[1]<=0:self.active=None;return None
            a.update(values=copy.deepcopy(values),needs_status=False)
        if a['needs_status']:
            if kind is None:return action(actor._key('menu'),'status','ちからの種を優先使用する前に、現在のちからと最大値を主メニューで確認する。')
            if kind in ('inventory','item_actions'):return action(actor._key('cancel'),'status','種や毒消し草を使う前に一覧を戻り、現在のちからを確認する。')
            self.active=None;return None
        if kind is None:return action(actor._key('menu'),'status','ちからの種の優先使用を続けるため主メニューを開く。')
        if kind=='main_menu':
            target=actor._cursor_toward(native,'道具')
            return action(actor._key('confirm') if target=='selected' else target,'inventory','現在のちからを確認したので、道具一覧へ1段階進む。') if target else None
        if kind=='inventory':
            wanted={SEED} if a['values'][0]==a['values'][1] else ANTIDOTES
            target=next((r for r in rows if r.get('display_name') in wanted and not r.get('identification_required')),None)
            if not target:
                signature=tuple((r.get('display_name'),r.get('quantity')) for r in rows)
                if signature not in a['pages'] and len(a['pages'])<20:
                    a['pages'].add(signature)
                    key=next((k for k,v in actor.effects.bindings.items() if v.startswith('右') and k in actor.cfg.allowed_keys),None)
                    return action(key,'page','種または毒消し草が現在ページにないため次のページを1回確認する。同じページなら反復しない。')
                self.active=None;return None
            a['target']=copy.deepcopy(target)
            direction=actor._cursor_toward(native,target,vertical=True)
            reason='ちからが最大なので、ちからの種を最優先で使用する。' if wanted=={SEED} else 'ちからが低下しているため種を使わず、先に毒消し草で最大値へ戻す。'
            return action(actor._key('confirm') if direction=='selected' else direction,'open' if direction=='selected' else 'navigate',reason) if direction else None
        if kind=='item_actions' and a['phase']=='actions' and a['target']:
            target=actor._cursor_toward(native,'飲む')
            return action(actor._key('confirm') if target=='selected' else target,'consume' if target=='selected' else 'navigate','選んだ種または毒消し草の「飲む」を確認して1回実行し、次にちからを再確認する。') if target else None
        return None
    def feedback(self,row):
        a=self.active;op=row.get('operation',{})
        if not a:return
        if op.get('selected_source')!='strength_growth':return
        accepted=row.get('executed') and not row.get('dry_run') and row.get('turn_kind')!='aborted' and row.get('input_receipt',{}).get('os_submission')=='accepted' and row.get('observed_keys')==op.get('selected_keys')
        if not accepted:self.active=None;return
        if a['pending']=='open':a['phase']='actions'
        elif a['pending']=='consume':a.update(needs_status=True,values=None,target=None,phase='inventory',pages=set())
        a['pending']=None
