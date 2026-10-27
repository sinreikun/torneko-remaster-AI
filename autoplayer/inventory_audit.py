"""Bounded inventory review before exploration; observations, never assumed effects."""
import copy,time

class InventoryAudit:
    INTERVAL=128
    def __init__(self):
        self.next_step=0;self.active=None;self.last=None;self.serial=0
    def finish(self,status,step):
        self.last={**self.context(), 'status':status,'finished_step':step}
        if self.active and self.active.get('analysis_started') is not None:self.last['analysis_elapsed_ms']=round((time.monotonic()-self.active['analysis_started'])*1000)
        self.active=None;self.next_step=step+self.INTERVAL
    def analyse_interface(self,current):
        self.serial+=1
        previous=self.active or {}
        self.active={**previous,'id':self.serial,'scope':copy.deepcopy((current.get('control_revision'),current.get('operator_guidance',{}).get('revision',0),current.get('floor'))),
          'started_step':current['step'],'until':current['step']+8,'stage':'analyse','metrics':copy.deepcopy(previous.get('metrics',{})),
          'pages':copy.deepcopy(previous.get('pages',{})),'coverage':'selected_item_action_menu','analysis_started':time.monotonic(),'analysis_step':current['step'],'page_pending':False}
    def prepare(self,state,current,native,review):
        step=current['step'];kind=current.get('interface')
        danger=any(max(abs(x),abs(y))<=1 for x,y in state.get('minimap',{}).get('enemies',[]))
        hp=state.get('metrics',{}).get('hp',{});food=state.get('metrics',{}).get('fullness',{})
        danger=danger or bool(hp.get('values') and not hp.get('retained') and hp['values'][0]/max(1,hp['values'][1])<.3 or food.get('values') and not food.get('retained') and food['values'][0]<=15)
        scope=(current.get('control_revision'),current.get('operator_guidance',{}).get('revision',0),current.get('floor'))
        if self.active and ((self.active['scope'][:2]!=scope[:2] or self.active['scope'][2] and scope[2] and self.active['scope'][2]!=scope[2]) or current.get('region')!='dungeon' or danger):self.finish('interrupted_current_context',step)
        if not self.active and step>=self.next_step and current.get('region')=='dungeon' and state.get('eligible') and not kind and not danger:
            self.serial+=1;self.active={'id':self.serial,'scope':copy.deepcopy(scope),'started_step':step,'until':step+96,'stage':'collect','metrics':{},'pages':{},'coverage':'unconfirmed','page_pending':False}
        a=self.active
        if not a:return
        if a['stage']=='collect' and step>a['until']:self.finish('observation_budget_exhausted',step);return
        for k,v in state.get('metrics',{}).items():
            if v.get('values') is not None and not v.get('retained'):a['metrics'][k]=copy.deepcopy(v)
        if kind!='inventory' or a['stage']!='collect':return
        rows=native.get('_current_inventory_rows',[])
        if not rows:
            count=(state.get('retained_state',{}).get('last_observed_inventory') or {}).get('occupancy') or {}
            if count.get('count')==0:a.update(stage='analyse',coverage='observed_empty',analysis_started=time.monotonic(),analysis_step=step)
            return
        signature=tuple(r.get('display_name','') for r in rows)
        known=all(r.get('known_description',{}).get('status')=='description_text_observed' or review and (r['display_name'],round(r['box'][1]/8)) in review['seen'] for r in rows if r.get('box'))
        if not known and not (review and (step>review['until'] or review['moves']>=review.get('max_moves',40))):return
        count=(state.get('retained_state',{}).get('last_observed_inventory') or {}).get('occupancy') or {}
        if review and (step>review['until'] or review['moves']>=review.get('max_moves',40)):
            a['pages'][signature]=copy.deepcopy(rows);a.update(stage='analyse',coverage='description_budget_exhausted',page_pending=False)
        elif a['page_pending'] and not a.get('page_submitted'):return
        elif a['page_pending'] and signature in a['pages']:
            a.update(stage='analyse',coverage='incomplete_or_page_did_not_change',page_pending=False)
        else:
            a['pages'][signature]=copy.deepcopy(rows);a['page_pending']=False
            total=sum(len(v) for v in a['pages'].values())
            if count.get('count') is not None and total>=count['count']:
                a.update(stage='analyse',coverage='observed_count_matches')
            elif len(a['pages'])>=20:a.update(stage='analyse',coverage='page_budget_exhausted')
            else:a['page_pending']=True;a['page_submitted']=False
        if a['stage']=='analyse':a['analysis_started']=time.monotonic();a['analysis_step']=step
    def context(self):
        a=self.active
        if not a:return {'active':False,'next_step':self.next_step,'last_review':copy.deepcopy(self.last)}
        return {'active':True,'id':a['id'],'stage':a['stage'],'started_step':a['started_step'],'analysis_step':a.get('analysis_step'),'coverage':a['coverage'],
          'metrics':copy.deepcopy(a['metrics']),'selected_item_quote':a.get('selected_item_quote'),'missing_status':[k for k in ('strength','sword','shield','experience') if k not in a['metrics']],
          'pages':[copy.deepcopy(rows) for rows in a['pages'].values()],
          'instruction':'収集した状態・品の用途・装備と特殊効果を比較して次の具体的操作を選ぶ。名前不明でも説明や装備という使用方法は種別の根拠になる。未確認ページや装備状態を推測で埋めない。確認済みの説明を再表示しない。'}
    def awaiting_analysis(self,supervisor,step):
        a=self.active
        if not a or a['stage']!='analyse':return False
        plan=supervisor.plan or {}
        if plan.get('inventory_audit_id')==a['id']:
            self.finish('analysed',step);return False
        completed=getattr(supervisor,'last_completed',None) or {}
        if completed.get('audit_id')==a['id'] and completed.get('error') or time.monotonic()-a['analysis_started']>95:
            self.finish('analysis_unavailable',step);return False
        supervisor.next_review=min(supervisor.next_review,step)
        return True
