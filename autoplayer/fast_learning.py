"""Event-triggered reasoning with wide periodic reviews; OCR stays per-turn."""
import hashlib,re,copy
from .interfaces import compact_text,torneko_menu_kind,result_screen

class FastLearningGate:
    def __init__(self,interval=128):self.interval=interval;self.last_call=None;self.floor=None;self.guidance=None;self.events={}
    def evaluate(self,turn,native,state,guidance):
        native=native or {};state=state or {}
        linked=not (native.get('frame_sha256') and state.get('frame_sha256') and native['frame_sha256']!=state['frame_sha256'])
        if native.get('status')!='ok' or not linked:native={'status':'unavailable','lines':[]}
        text='\n'.join(compact_text(l.get('text','')) for l in native.get('lines',[]))
        floor=(state or {}).get('metrics',{}).get('floor',{}).get('values')
        reason=None
        if self.last_call is None:reason='initial_review'
        elif guidance.get('revision')!=self.guidance:reason='operator_guidance_changed'
        elif floor and self.floor and floor!=self.floor:reason='confirmed_floor_changed'
        elif turn-self.last_call>=self.interval:reason='periodic_review'
        meaningful=bool(re.search(r'はい|いいえ|選択|どれ|ゲームクリア|持ち帰|脱出|帰還|地上に戻|倒された|力尽き|箱.{0,12}(?:入手|手に[入い]|拾|ひろ|取|とっ|所持)',text)
            or torneko_menu_kind(native) in ('inventory','item_actions') or result_screen(native))
        hud=re.compile(r'^(?:HP[^0-9]{0,3}\d+/\d+|Lv\d+|満腹度(?:\d+%)?|ちから(?:\d+/\d+)?|剣の強さ\d*|盾の強さ\d*|経験値\d*|最深階\d*|\d+(?:/\d+|G|階|%)?)$',re.I)
        event_text='\n'.join(line for line in text.splitlines() if not hud.fullmatch(line))
        signature=hashlib.sha256(event_text.encode()).hexdigest()[:16] if meaningful else None
        if not reason and signature:
            previous=self.events.get(signature)
            if previous is None or turn-previous>=16:reason='choice_or_objective_event'
        if reason:
            self.last_call=turn;self.guidance=guidance.get('revision');self.floor=copy.deepcopy(floor or self.floor)
            if signature:self.events[signature]=turn
            while len(self.events)>64:self.events.pop(next(iter(self.events)))
        elif floor:self.floor=copy.deepcopy(floor)
        return {'llm_requested':bool(reason),'reason':reason or 'local_input_and_observation','review_interval':self.interval,
            'next_review_in':max(0,self.interval-(turn-self.last_call)),'turn':turn,
            'note':'通常手は入力・画面・OCR・地図・反応の収集のみ。選択肢や重要変化と広い間隔の確認でLLMを使う。'}
