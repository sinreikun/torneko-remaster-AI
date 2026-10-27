"""Task-specific inference summaries. Full evidence remains in local records."""
import copy,json

def pick(value,names):
    return {k:copy.deepcopy(value[k]) for k in names if isinstance(value,dict) and k in value}

def metrics(value):
    return {k:pick(v,('values','retained','status')) for k,v in (value or {}).items() if isinstance(v,dict) and v.get('values') is not None}

def compact_supervision_prompt(prompt,budget=6500,tight=False):
    if prompt.get('context_contract')=='supervision_summary_v1':result=copy.deepcopy(prompt)
    else:
        source=prompt.get('state_ledger',{});current=source.get('current',{});world=source.get('world_knowledge',{})
        audit=source.get('inventory_audit') or current.get('inventory_audit') or {}
        rows=[r for page in audit.get('pages',[]) for r in page] if audit.get('active') else []
        if not rows:rows=(source.get('last_observed_inventory') or {}).get('rows',[])
        inventory=[]
        for row in rows[:20]:
            item=pick(row,('display_name','quote','quantity','equipped','selected','name_status','true_name'))
            description=row.get('known_description') or {}
            if description.get('status')=='description_text_observed':
                text=' / '.join(description.get('quotes',[]));item['description_ocr']=text[:160]
                if len(text)>160:item['description_excerpt']=True
            if row.get('equipment_affordance'):item['available_equipment_action']=row['equipment_affordance'].get('action')
            inventory.append(item)
        retained=current.get('retained_state',{})
        compact_current=pick(current,('step','region','floor','interface','text','selection_text','selection_line_index','contacts','visible_map_targets','fact_rule'))
        compact_current['metrics']=metrics(current.get('metrics'))
        compact_current['position']=copy.deepcopy(current.get('player_cell'))
        compact_current['retained_metrics']=metrics(retained.get('last_observed_metrics') or current.get('last_known_metrics'))
        compact_current['refresh_reasons']=copy.deepcopy(retained.get('refresh_reasons',[]))
        exploration=current.get('floor_exploration',{})
        compact_current['floor_task']=pick(exploration.get('goal',{}),('phase','reason','target_cell','needs_review','item_recheck_needed'))
        compact_current['floor_counts']=pick(exploration,('known_count','visited_count','primary_frontier_count'))
        ledger={'current':compact_current,'inventory':{'status':'current_rows' if source.get('inventory_observed_now') else 'last_observation_or_review_pages_not_current_confirmation','rows':inventory},
          'inventory_audit':pick(audit,('active','id','stage','coverage','selected_item_quote','missing_status','instruction')),
          'active_execution':pick(source.get('active_execution',{}),('recovery_active','item_action','recovery_item','description_review')),
          'active_plan':pick(source.get('active_plan',{}),('objective','skill','reason','test','hypothesis','interface_role','interface_quote')),
          'review_feedback':pick(source.get('review_feedback',{}),('last_error','reason'))}
        if audit.get('active'):ledger['inventory_audit']['observed_metrics']=metrics(audit.get('metrics'))
        equipment=world.get('equipment_review',{})
        ledger['equipment_comparisons']=[pick(x,('display_name','before','after','metric_changes','status','attribution')) for x in equipment.get('observed_comparisons',[])[-2:]]
        ledger['equipment_pending']=pick(equipment.get('verification_pending') or {},('display_name','command','origin_step'))
        names={r.get('id'):r.get('display_name') for r in rows}
        ledger['item_use_experience']=[{'display_name':names.get(x.get('item_id')),'command':x.get('command'),
          'observed_association':pick(x.get('estimate',{}),('trials','confounded_trials','dimensions','status'))} for x in world.get('item_value_estimates',[])[:4] if x.get('item_id') in names]
        ledger['recent_outcomes']=[pick(x,('step','keys','role','effects','progress')) for x in source.get('recent',[])[-3:]]
        ledger['action_effects']=[pick(x,('keys','role','samples','mean_observed_value','move_rate','no_effect_rate')) for x in source.get('action_effects',[])[:12]]
        guide=prompt.get('game_guide',{})
        rules=[{k:(v[:180] if isinstance(v,str) else v) for k,v in pick(r,('id','precondition','expected_effect','verify_or_revise')).items()} for r in (guide.get('rules',[])[:3]+guide.get('foundations',[])[:2])]
        result=pick(prompt,('task','goal','operator_guidance','current_key_bindings','instruction','execution_order','required_output_schema'))
        result.update(context_contract='supervision_summary_v1',state_ledger=ledger,game_rules=rules,
          output_limits={'budget_turns':'整数2〜32','test_attempts':'整数1〜4','exploration_fraction':'0〜0.5の小数。通常0.125'},
          evidence_rule='現在画面と保持値・説明OCR・実測を区別。選択行と操作の照合は実行器が担当。概要にない情報は未確認。全記録と画像はローカルに保持。')
    size=lambda:len(json.dumps(result,ensure_ascii=False,separators=(',',':')))
    ledger=result['state_ledger']
    if tight or size()>budget:
        for key in ('action_effects','recent_outcomes','equipment_comparisons','item_use_experience'):
            ledger.pop(key,None)
        result.pop('game_rules',None)
    if tight or size()>budget:
        for row in ledger.get('inventory',{}).get('rows',[]):
            if 'description_ocr' in row:
                row['description_ocr']=row['description_ocr'][:60];row['description_excerpt']=True
        ledger['current'].pop('floor_counts',None)
    if size()>budget:
        for row in ledger.get('inventory',{}).get('rows',[]):
            row.pop('description_ocr',None);row['description_omitted_for_budget']=True
        ledger['current'].pop('retained_metrics',None)
    if size()>budget:raise ValueError('監督LLMの必須情報が送信予算を超えています。現在画面・目標・作戦指示を切り落とさず送信を見送りました。')
    return result
