"""User supplied Torneko exterior rules, independent of tentative pose quality."""
from collections import Counter
from dataclasses import replace
from .controls import is_torneko, REFERENCE
from .interfaces import torneko_menu_kind, result_screen, PROTECTED_CONFIRMATION

class ExteriorActions:
    def __init__(self,cfg,navigation):
        self.cfg=cfg;self.navigation=navigation;self.location='unclassified';self.attempts=Counter();self.context={}
    def observe(self,state,native,scene='unknown'):
        state=state or {};native=native or {}
        if not is_torneko(self.cfg.window_title):self.context={'active':False};return self.context
        # A missing minimap alone never establishes an exterior: a current
        # scene hypothesis or independently read town menu is required. Retain dungeon evidence across
        # OCR dropouts until an explicit title/result/episode boundary.
        if result_screen(native) or scene in ('title','defeat','victory'):
            self.location='unclassified';self.attempts.clear()
        elif state.get('minimap',{}).get('status')=='observed' or state.get('metrics',{}).get('floor',{}).get('values'):
            self.location='dungeon'
        elif self.location!='dungeon' and (scene=='gameplay' or torneko_menu_kind(native)=='main_menu' or
                scene in ('unknown','menu') and (native.get('frame_sha256') or state.get('frame_sha256'))):
            # Unknown image classifications are exploration hypotheses, not
            # permission to research menus. Explicit title/result/loading
            # classifications and independently read interfaces take priority.
            self.location='outside'
        active=self.location=='outside' and scene not in ('title','loading','defeat','victory')
        actions=[];mode='exit_exploration'
        text='\n'.join(l.get('text','') for l in native.get('lines',[]))
        confirm=self.navigation.confirm
        cancel=next((k for k,v in (self.cfg.key_bindings or REFERENCE['keys']).items() if ('キャンセル' in v or 'cancel' in v.lower()) and k in self.cfg.allowed_keys),None)
        if active:
            if PROTECTED_CONFIRMATION.search(text):
                mode='protected_cancel';actions=[[cancel]] if cancel else []
            elif torneko_menu_kind(native) or state.get('menu_evidence'):
                # Closing an already-open menu is recovery, not an exploration
                # candidate. Never navigate or research its items outside.
                mode='close_existing_menu';actions=[[cancel]] if cancel else []
            elif (scene=='dialog' and any(sum(c.isalpha() for c in l.get('text',''))>=8 for l in native.get('lines',[]))) or ('はい' in text and 'いいえ' in text):
                mode='advance_existing_dialog';actions=[[confirm]] if confirm else []
            else:
                movement=[k for k in self.navigation.keys.values() if k]
                actions=[[k] for k in movement]
                if self.navigation.face:actions += [[self.navigation.face,k] for k in movement]
                if confirm:actions.append([confirm])
        self.context={'active':active,'location':self.location,'mode':mode,'actions':actions,
            'rule':'ダンジョン外の固定地形では出口への移動が唯一の局所目標。探索候補は移動・方向転換・通過を確認する決定入力だけ。メニュー・射撃・ダッシュ・足踏み・地図表示を試さない。既に開いたメニューと保護確認は取消で閉じる。全体目標は維持する。'}
        return self.context
    def enforce(self,decision,blocked=()):
        context=self.context
        if not context.get('active') or decision.status!='continue':return decision,{}
        actions=context['actions']
        if not actions:raise RuntimeError('ダンジョン外の移動・決定、または既存画面の取消に対応する許可キーがありません。')
        if decision.keys in actions:return decision,{}
        recent={tuple(k) for k in blocked}
        candidates=[k for k in actions if tuple(k) not in recent] or actions
        keys=min(candidates,key=lambda k:(self.attempts[tuple(k)],len(k),actions.index(k)))
        return replace(decision,keys=list(keys),hold_ms=min(80,self.cfg.max_hold_ms),wait_ms=min(600,self.cfg.max_wait_ms),
            intent={},forecast={},plan=[],progress=[],memory_updates=[],goal_evidence=[],knowledge_assessments=[],progress_test={},
            subgoal='既存の画面を閉じて出口探索へ戻る' if context['mode']!='exit_exploration' else '現在領域の出口へ向かう',
            reason='ダンジョン外の行動制限に従い、移動・方向転換・決定、または既存画面を閉じる入力へ変更する。'),{
                'source':'exterior_action_filter','mode':context['mode'],'previous_keys':decision.keys,'selected_keys':list(keys),'learning':False}
    def sent(self,keys):
        if self.context.get('active'):
            self.attempts[tuple(keys)]+=1
