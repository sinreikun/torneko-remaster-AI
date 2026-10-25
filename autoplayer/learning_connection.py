"""Per-input accounting of candidate reuse, outcome updates and experience writes."""
import copy


def candidate(choice,decision):
    choice=choice or {};selected=choice.get('selected') or {}
    if choice.get('source') not in ('learned_policy','learned_interface_policy','learned_outcome_prediction'):return {'offered':False,'source':choice.get('source')}
    return {'offered':True,'source':choice['source'],'keys':list(decision.keys),
        'prior_trials':selected.get('trials',selected.get('samples',0)),'prior_q':selected.get('q'),
        'selection_basis':choice.get('selection_basis','interface_response'),
        'state':(choice.get('context') or {}).get('state')}


def report(row,before,after):
    proposed=copy.deepcopy(row.get('learning_candidate') or {'offered':False})
    source=(row.get('active_input') or {}).get('source')
    actual=row.get('observed_keys',[]);same=bool(proposed.get('offered') and proposed.get('keys')==actual)
    retained=bool(row.get('executed') and same and source in (None,'uncertain_input_trial'))
    training=row.get('policy_learning') or {};review=row.get('experience_review') or {}
    before=before or {};after=after or {}
    modes=[s.get('observation_mode','normal') for s in (before,after)]
    map_pair=all(s.get('minimap',{}).get('status')=='observed' for s in (before,after)) and modes[0]==modes[1]
    spatial=map_pair and before.get('eligible') and after.get('eligible')
    scope='spatial' if spatial else 'hud_or_text' if any(s.get('metrics',{}).get(k) for s in (before,after) for k in ('hp','floor')) else 'interface_or_image'
    return {'candidate':proposed,'executed_keys':list(actual),'candidate_keys_equal':same,'policy_candidate_retained':retained,
        'retained_candidate_has_experience':retained and proposed.get('prior_trials',0)>0,
        'replacement_source':source if proposed.get('offered') and not retained else None,
        'outcome_update':{'status':training.get('status','not_available'),'reason':training.get('reason'),'scope':scope,
            'map_pair_observed':map_pair,'observation_modes':modes,'reward':training.get('reward'),
            'q_before':training.get('q_before'),'q_after':training.get('q_after'),'net_signals':training.get('net_signals',{}),
            'interface_information_gain':training.get('interface_information_gain',0),'goal_success_verified':False},
        'experience_recorded':review.get('status')=='recorded','experience_status':review.get('status','not_recorded'),
        'model_report_memory_requested':bool(row.get('learning') is not False),'note':'外部の行動価値と経験の接続。LLM重みの学習やゴール成功の証明ではない。'}


def summary(value):
    c=value.get('candidate',{});u=value.get('outcome_update',{})
    selection='候補なし' if not c.get('offered') else '方策候補を採用' if value.get('policy_candidate_retained') else '方策候補を上書き('+str(value.get('replacement_source') or '別処理')+')'
    status={'trained':'方策更新','censored':'比較不能・未採点','not_trained':'方策未更新'}.get(u.get('status'),'更新なし')
    return f"学習接続: {selection} / {status} [{u.get('scope','不明')}] / 経験{'保存' if value.get('experience_recorded') else '未保存'}"
