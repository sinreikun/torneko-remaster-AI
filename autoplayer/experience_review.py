"""Bounded observed experience summaries, independent of model report learning."""
import copy,json


def outcome_learning_allowed(row):
    if str(row.get('event','')).startswith('diagnostic') or row.get('kind') in ('compatibility','separation'):
        return False
    return row.get('learning') is not False or (row.get('active_input') or {}).get('source') in ('information_collection','active_input_trial')


class ExperienceReview:
    def __init__(self,memory=None):
        self.db=memory.db if memory else None;self.profile=memory.profile if memory else 'session';self.rows=[]
        if self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS experience_review_v1(profile TEXT PRIMARY KEY,data TEXT)')
            record=self.db.execute('SELECT data FROM experience_review_v1 WHERE profile=?',(self.profile,)).fetchone()
            if record:self.rows=json.loads(record[0])[-64:]
            self.db.commit()
    def record(self,row,before,after):
        if not row.get('executed') or row.get('dry_run') or row.get('turn_kind')=='aborted' or row.get('action_kind')!='input' or not outcome_learning_allowed(row):return {'status':'excluded'}
        if (row.get('input_receipt') or {}).get('os_submission')!='accepted':return {'status':'unverified_submission'}
        before=before or {};after=after or {}
        if not before.get('observable') or not after.get('observable'):return {'status':'missing_observation'}
        metrics0=before.get('metrics',{});metrics1=after.get('metrics',{})
        # Track persistence without assigning a later effect to one earlier key.
        for prior in self.rows[-4:]:
            persistence=prior.setdefault('metric_persistence',{})
            for key,change in prior['metric_changes'].items():
                if key in metrics1 and metrics1[key].get('values')==change['after']:
                    persistence[key]=min(4,persistence.get(key,0)+1)
        changes={k:{'before':metrics0[k].get('values'),'after':metrics1[k].get('values')} for k in metrics0.keys()&metrics1.keys() if metrics0[k].get('values')!=metrics1[k].get('values')}
        same_mode=before.get('observation_mode','normal')==after.get('observation_mode','normal')
        signals=copy.deepcopy((row.get('policy_learning') or {}).get('net_signals',{})) if same_mode else {}
        entry={'keys':list(row.get('observed_keys',[])),'situation':(row.get('information_exploration') or {}).get('situation'),
            'metric_changes':changes,'net_signals':signals,'reward_proxy':(row.get('policy_learning') or {}).get('reward'),
            'forecast_status':(row.get('forecast_evaluation') or {}).get('status'),'natural_changes':copy.deepcopy(row.get('natural_metric_changes',{})),
            'source_frame':after.get('frame_sha256'),'observation_mode_comparable':same_mode,
            'attribution':'observed_sequence_not_proven_cause'}
        self.rows=(self.rows+[entry])[-64:]
        if self.db:
            self.db.execute('INSERT INTO experience_review_v1 VALUES(?,?) ON CONFLICT(profile) DO UPDATE SET data=excluded.data',(self.profile,json.dumps(self.rows,ensure_ascii=False)));self.db.commit()
        return {'status':'recorded','retained':len(self.rows),'attribution':entry['attribution']}
    def context(self):
        groups={}
        for r in self.rows:
            key=(r['situation'],tuple(r['keys']));g=groups.setdefault(key,{'situation':r['situation'],'keys':r['keys'],'observations':0,'positive_proxy':0,'negative_proxy':0,'forecast_mismatches':0,'metric_examples':[]})
            g['observations']+=1;reward=r['reward_proxy'];g['positive_proxy']+=int(reward is not None and reward>0);g['negative_proxy']+=int(reward is not None and reward<0)
            g['forecast_mismatches']+=int(r['forecast_status']=='mismatch')
            if r['metric_changes']:g['metric_examples']=(g['metric_examples']+[r['metric_changes']])[-2:]
        return {'retained_observations':len(self.rows),'action_groups':list(groups.values())[-24:],'recent_observations':copy.deepcopy(self.rows[-8:]),
            'instruction':'これは実入力と観測の要約で、成功や因果の確定ではない。現在の画面と作戦指示を優先する。反復・予測不一致・持続するステータス変化を比較し、全体目標へ有効そうな行動と未確認の仮説を分け、次に試す具体的なキーと期待する変化をforecastに設定する。見た目の変化だけを攻略成果にしない。過去の予測が外れたなら修正する。'}
