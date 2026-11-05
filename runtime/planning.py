"""Private static recipe cache. Never cache user-specific argument values."""
import json
import sqlite3
from .common import digest
from .cases import validate_snapshot


def recipe(value):
    # ABI and parameter *sources* only. No calldata, signatures, proof values,
    # recipient, nonce, referrer defaults, or prepared wallet requests.
    base={'function_signature','argument_sources','output_check'}
    if not base<=set(value) or set(value)-base-{'followup_checks'}:
        raise ValueError('recipe must not contain personal values or executable calldata')
    if not isinstance(value['function_signature'], str) or '(' not in value['function_signature']:
        raise ValueError('explicit ABI function signature required')
    if not isinstance(value['argument_sources'],list) or any(x not in {
            'quantity','recipient','wallet','fresh_proof','fresh_signature','fresh_nonce',
            'fresh_price','user_referrer','activity_id'} for x in value['argument_sources']):
        raise ValueError('only fresh, named argument sources may be cached')
    if value['output_check'] not in {'asset_balance_delta','nft_ownership','verified_position'}:
        raise ValueError('explicit output verification method required')
    tasks=value.get('followup_checks',[])
    if not isinstance(tasks,list):raise ValueError('followup checks must be a list')
    seen=set()
    for task in tasks:
        if (set(task)!={'id','kind','schedule_source','check'} or not isinstance(task['id'],str)
            or not task['id'] or task['id'] in seen
            or task['kind'] not in {'claim','reveal','unlock'}
            or task['schedule_source'] not in {'protocol_schedule','receipt_deadline'}
            or task['check'] not in {'claimable','revealed','unlocked'}):
            raise ValueError('invalid unbound followup recipe')
        seen.add(task['id'])
    return value


class TemplateStore:
    def __init__(self,path):
        self.db=sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS mint_templates (binding TEXT PRIMARY KEY, record TEXT)')
    def close(self): self.db.close()
    def record(self,snapshot,value,case_id,host):
        validate_snapshot(snapshot);recipe(value)
        if host.review_template(snapshot,value,case_id) is not True:
            raise PermissionError('independent ABI/argument/output review required')
        record=dict(recipe=value,case_id=case_id,snapshot=snapshot)
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO mint_templates VALUES (?,?)',(digest(snapshot),json.dumps(record)))
        return record
    def find(self,snapshot,match):
        if match['route']!='reuse_static_analysis':return None
        row=self.db.execute('SELECT record FROM mint_templates WHERE binding=?',(digest(snapshot),)).fetchone()
        if not row:return None
        record=json.loads(row[0])
        if not any(c['case_id']==record['case_id'] and c['route']=='reuse_static_analysis' for c in match['candidates']):
            return None
        return record
