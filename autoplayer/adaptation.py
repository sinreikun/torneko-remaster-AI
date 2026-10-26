"""State-local input-duration trials. Response is association, never a win reward."""
import hashlib
import json


class DurationLearner:
    def __init__(self, memory, cfg):
        self.memory = memory
        self.cfg = cfg
        memory.db.execute('''CREATE TABLE IF NOT EXISTS input_durations(
            profile TEXT,state TEXT,key TEXT,ms INTEGER,trials INTEGER,responses INTEGER,
            PRIMARY KEY(profile,state,key,ms))''')
        memory.db.commit()

    def state(self, decision, observation=None):
        # Mechanical context uses observations, not changing model-generated subgoal labels.
        return hashlib.sha256(json.dumps(['one-cell-input-v2',decision.scene,decision.keys,(observation or {}).get('metrics',{}).get('floor',{}).get('values'),(observation or {}).get('minimap',{}).get('source_size')], ensure_ascii=False).encode()).hexdigest()[:24]

    def choose(self, decision, observation=None):
        if decision.scene != 'gameplay' or len(decision.keys) != 1 or decision.status != 'continue' or decision.risk != 'normal' or decision.confidence < self.cfg.min_confidence:
            return None
        import re
        from .controls import ACTIONS,is_torneko
        bindings=self.cfg.key_bindings or (ACTIONS if is_torneko(self.cfg.window_title) else {})
        if not re.search(r'移動|move|前進|後退|^上|^下|^左|^右',bindings.get(decision.keys[0],''),re.I):return None
        state = self.state(decision,observation)
        values = sorted({min(self.cfg.max_hold_ms, ms) for ms in (40,80,120,180,250)})
        rows = self.memory.db.execute('SELECT ms,trials,responses FROM input_durations WHERE profile=? AND state=? AND key=?',
                                      (self.memory.profile, state, decision.keys[0])).fetchall()
        stats = {ms:(trials,responses) for ms,trials,responses in rows}
        # Bounded trials; once response is repeated, prefer its shortest duration.
        established = [ms for ms in values if stats.get(ms,(0,0))[0] >= 3 and stats[ms][1]/stats[ms][0] >= .8]
        chosen = min(established) if established else min(values, key=lambda ms:(stats.get(ms,(0,0))[0],ms))
        requested = decision.hold_ms
        decision.hold_ms = chosen
        return {'state': state, 'key': decision.keys[0], 'ms': chosen, 'requested_ms': requested,
                'interpretation': 'response_association_not_goal_success'}

    def record(self, trial, row):
        receipt = row.get('input_receipt') or {}
        if (not trial or not row.get('executed') or row.get('dry_run') or row.get('turn_kind') == 'aborted'
                or receipt.get('os_submission') != 'accepted' or row.get('observed_keys') != [trial['key']]
                or row.get('neutral_before', {}).get('kind') not in ('normal','no_change','timeout')):
            return
        baseline = row['neutral_before'].get('raw_change', 0)
        if row.get('operation'):
            delta=(row.get('action_effect_learning') or {}).get('effects',{}).get('player_delta')
            if delta is None:return
            expected={'w':[0,-1],'s':[0,1],'a':[-1,0],'d':[1,0]}.get(trial['key'])
            if expected is None or delta==[0,0]:return # A wall or unknown state is not a duration failure.
            before=row.get('policy_state') or {};after=row.get('policy_after') or {}
            if before and after and before.get('metrics',{}).get('floor',{}).get('values')!=after.get('metrics',{}).get('floor',{}).get('values'):return
            response=int(delta==expected)
        else:response = int(row.get('raw_change', 0) > max(.01, 4*baseline))
        with self.memory.db:
            self.memory.db.execute('''INSERT INTO input_durations VALUES(?,?,?,?,1,?)
                ON CONFLICT(profile,state,key,ms) DO UPDATE SET trials=trials+1,responses=responses+excluded.responses''',
                (self.memory.profile,trial['state'],trial['key'],trial['ms'],response))

    def context(self):
        rows = self.memory.db.execute('SELECT state,key,ms,trials,responses FROM input_durations WHERE profile=? ORDER BY trials DESC LIMIT 8',
                                      (self.memory.profile,)).fetchall()
        return {'duration_trials':[dict(zip(('state','key','ms','trials','response_associations'),r)) for r in rows],
                'note':'時間調整の観測関連。自然変化を差し引く保守的なしきい値。画面差分は入力受理・攻略進捗・成功を証明しない。'}
