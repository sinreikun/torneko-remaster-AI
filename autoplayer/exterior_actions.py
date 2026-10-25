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
        # Current displays decide the region. A previous dungeon label or a
        # retained floor value is not evidence of a display on this frame.
        floor=state.get('metrics',{}).get('floor',{})
        confirmation=state.get('floor_confirmation',{})
        supplied=state.get('display_evidence',{})
        floor_present=bool((floor.get('values') and not floor.get('retained')) or
            (confirmation.get('observed') is not None and not confirmation.get('rejected_low_confidence')) or supplied.get('floor_present'))
        minimap=state.get('minimap',{})
        map_present=bool((minimap.get('status')=='observed' and not minimap.get('retained')) or supplied.get('map_present'))
        evidence={'floor_present':floor_present,'map_present':map_present,
            'white_frame_visible':state.get('white_frame_evidence',{}).get('visible'),'basis':'current_display_presence'}
        if floor_present or map_present:self.location='dungeon'
        elif result_screen(native) or scene in ('title','loading','defeat','victory'):
            self.location='unclassified';self.attempts.clear()
        elif scene=='gameplay' or torneko_menu_kind(native)=='main_menu' or native.get('frame_sha256') or state.get('frame_sha256') or native.get('lines'):
            self.location='outside'
        else:self.location='unclassified'
        active=self.location=='outside' and scene not in ('title','loading','defeat','victory')
        actions=[];mode='exit_exploration'
        text='\n'.join(l.get('text','') for l in native.get('lines',[]))
        confirm=self.navigation.confirm
        cancel=next((k for k,v in (self.cfg.key_bindings or REFERENCE['keys']).items() if ('キャンセル' in v or 'cancel' in v.lower()) and k in self.cfg.allowed_keys),None)
        clear_screen=state.get('white_frame_evidence',{}).get('visible') is False
        if active:
            if PROTECTED_CONFIRMATION.search(text):
                mode='protected_cancel';actions=[[cancel]] if cancel else []
            elif not clear_screen and (torneko_menu_kind(native) or state.get('menu_evidence')):
                # Closing an already-open menu is recovery, not an exploration
                # candidate. Never navigate or research its items outside.
                mode='close_existing_menu';actions=[[cancel]] if cancel else []
            elif not clear_screen and ((scene=='dialog' and any(sum(c.isalpha() for c in l.get('text',''))>=8 for l in native.get('lines',[]))) or ('はい' in text and 'いいえ' in text)):
                mode='advance_existing_dialog';actions=[[confirm]] if confirm else []
            else:
                movement=[k for k in self.navigation.keys.values() if k]
                actions=[[k] for k in movement]
                if confirm:actions.append([confirm])
        self.context={'active':active,'location':self.location,'mode':mode,'actions':actions,'scene_evidence':{**evidence,'outside_actionable':active and mode=='exit_exploration' and clear_screen},
            'rule':'ダンジョン外の固定地形では出口への移動が唯一の局所目標。探索候補は移動・通過を確認する決定入力だけ。その場での方向転換（Num5＋方向キー）を試さない。メニュー・射撃・ダッシュ・足踏み・地図表示を試さない。既に開いたメニューと保護確認は取消で閉じる。外部の固定地形には複数種類があり、出口の個数や形状は断定しない。全体目標は維持する。'}
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
            reason='ダンジョン外の行動制限に従い、移動・決定、または既存画面を閉じる入力へ変更する。'),{
                'source':'exterior_action_filter','mode':context['mode'],'previous_keys':decision.keys,'selected_keys':list(keys),'learning':False}
    def sent(self,keys):
        if self.context.get('active'):
            self.attempts[tuple(keys)]+=1
