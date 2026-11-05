"""Host-reviewed provider acceptance and independent research spending ledger."""
import json
import sqlite3
import time
from .common import digest
from .agents import READ_ROLES,WRITE_ROLES


class ServiceControls:
    def __init__(self,path,budget=None,clock=time.time):
        self.db=sqlite3.connect(path);self.budget=budget or {};self.clock=clock
        self.db.executescript('''CREATE TABLE IF NOT EXISTS provider_acceptance(provider TEXT,role TEXT,config TEXT,expires REAL,record TEXT,PRIMARY KEY(provider,role));
        CREATE TABLE IF NOT EXISTS research_spend(id TEXT PRIMARY KEY,period TEXT,cost INTEGER,record TEXT);''')
    def close(self):self.db.close()
    def accept(self,name,config,role,evidence,review,expires_at):
        if role not in READ_ROLES|WRITE_ROLES or role not in config.get('capabilities',[]):raise ValueError('unsupported capability')
        if (review.get('verified') is not True or not review.get('reviewer') or not review.get('evidence_ref')
                or not evidence or expires_at<=self.clock()):raise PermissionError('independent capability acceptance required')
        if role in WRITE_ROLES and not {'status','cancel'}<=set(config.get('task_control',[])):
            raise ValueError('write provider needs status and cancellation protocol; never test by spending')
        with self.db:self.db.execute('INSERT OR REPLACE INTO provider_acceptance VALUES (?,?,?,?,?)',
            (name,role,digest(config),expires_at,json.dumps(dict(evidence=evidence,review=review))))
    def probe(self,name,config,role,payload,invoke,verify,expires_at):
        if role not in READ_ROLES:raise PermissionError('capability probe cannot submit wallet writes')
        # invoke must apply configured disclosure and budget; verify is host-side.
        result=invoke(role,payload)
        if result.get('status')!='completed':raise ValueError('probe has not completed; capability remains unaccepted')
        review=verify(role,payload,result)
        self.accept(name,config,role,result,review,expires_at)
        return result
    def available(self,name,config,role):
        row=self.db.execute('SELECT config,expires FROM provider_acceptance WHERE provider=? AND role=?',(name,role)).fetchone()
        return bool(row and row[0]==digest(config) and row[1]>self.clock())
    def reserve(self,request_id,provider,role,quote=None):
        if role in WRITE_ROLES:return  # Wallet payment limits are a separate policy.
        period=time.strftime('%Y-%m-%d',time.gmtime(self.clock()))
        limit=self.budget.get('max_cost_units_per_day');calls=self.budget.get('max_calls_per_day')
        for value in [limit,calls]:
            if value is not None and (type(value) is not int or value<0):raise ValueError('budget must be nonnegative integer or null')
        cost=0
        if quote is not None:
            if type(quote.get('max_cost_units')) is not int or quote['max_cost_units']<0:raise ValueError('invalid cost ceiling')
            cost=quote['max_cost_units']
        if limit is not None and (quote is None or quote.get('currency')!=self.budget.get('currency')):
            raise PermissionError('cost budget requires an accepted cost ceiling in configured currency')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if self.db.execute('SELECT 1 FROM research_spend WHERE id=?',(request_id,)).fetchone():return
            used,spent=self.db.execute('SELECT COUNT(*),COALESCE(SUM(cost),0) FROM research_spend WHERE period=?',(period,)).fetchone()
            if calls is not None and used>=calls:raise PermissionError('daily research call budget exhausted')
            if limit is not None and spent+cost>limit:raise PermissionError('daily research cost budget exhausted')
            self.db.execute('INSERT INTO research_spend VALUES (?,?,?,?)',(request_id,period,cost,json.dumps(dict(provider=provider,role=role,quote=quote))))
        # Timeout/cancel never refunds an unverified charge. No token-price guesses.
