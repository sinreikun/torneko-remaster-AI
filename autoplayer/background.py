"""No focus fallback. Track OS delivery separately from screen response."""
import json


class BackgroundUnavailable(RuntimeError):
    """The available window-addressed capture/input path explicitly failed."""


class BackgroundEvidence:
    def __init__(self,memory):
        self.memory=memory
        memory.db.execute('CREATE TABLE IF NOT EXISTS background_evidence(profile TEXT PRIMARY KEY,data TEXT)')
        memory.db.commit()

    def update(self,row,signal=None):
        receipt=row.get('input_receipt') or {}
        if row.get('dry_run') or not row.get('executed') or row.get('turn_kind')=='aborted':return self.context()
        value=self.context()
        if receipt.get('os_submission')=='accepted':value['accepted_submissions']+=1
        background=receipt.get('background_before') is True and receipt.get('background_after') is True
        if background:
            value['background_submissions']+=1
            if signal is True:value['criterion_changes_in_background']+=1
        value['status']='response_observed_in_background' if value['criterion_changes_in_background'] else 'response_unconfirmed'
        with self.memory.db:self.memory.db.execute('INSERT OR REPLACE INTO background_evidence VALUES(?,?)',
                                                   (self.memory.profile,json.dumps(value,ensure_ascii=False)))
        return value

    def context(self):
        saved=self.memory.db.execute('SELECT data FROM background_evidence WHERE profile=?',(self.memory.profile,)).fetchone()
        return json.loads(saved[0]) if saved else {'status':'not_tested','accepted_submissions':0,'background_submissions':0,
            'criterion_changes_in_background':0,'input_backend':'window_message_only','capture_backend':'target_PrintWindow_only',
            'note':'OS受理と画面反応を分離。可視条件の変化も因果の証明ではない。無反応だけで非対応と判定しない。'}
