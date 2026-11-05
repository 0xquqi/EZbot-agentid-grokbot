"""Private recurring entitlements and append-only claim events; no wallet writes."""
import json
import math
import re
import sqlite3
import time
from .common import address, digest, uint


def stamp(value):
    if value is None:return None
    if type(value) not in {int,float} or not math.isfinite(value) or value<0:
        raise ValueError('invalid entitlement timestamp')
    return value


def evidence(fact):
    if fact.get('verified') is not True or not fact.get('evidence_ref'):
        raise PermissionError('independent entitlement evidence required')


class ClaimLedger:
    def __init__(self,path,clock=time.time):
        self.db=sqlite3.connect(path);self.clock=clock
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS claim_reviews(id TEXT PRIMARY KEY,owner TEXT,attempt_id TEXT,record TEXT);
        CREATE TABLE IF NOT EXISTS claim_entitlements(id TEXT PRIMARY KEY,owner TEXT,record TEXT);
        CREATE TABLE IF NOT EXISTS claim_attempts(owner TEXT,id TEXT,entitlement TEXT,record TEXT,PRIMARY KEY(owner,id));
        CREATE TABLE IF NOT EXISTS claim_events(id TEXT PRIMARY KEY,owner TEXT,record TEXT);
        CREATE TABLE IF NOT EXISTS claim_scans(owner TEXT,order_id TEXT,record TEXT,PRIMARY KEY(owner,order_id));
        ''')
    def close(self):self.db.close()
    def register(self,owner,value,review):
        evidence(review)
        if not review.get('reviewer'):raise PermissionError('entitlement reviewer required')
        required={'project_id','chain_id','wallet','contract','position_id','benefit_id','kind','recurring','conditions'}
        if not required<=value.keys():raise ValueError('entitlement identity and conditions required')
        if type(value['chain_id']) is not int or value['chain_id']<=0 or type(value['recurring']) is not bool:
            raise ValueError('explicit chain and recurrence required')
        if value['kind'] not in {'nft_mining','dividend','staking_reward','vesting','airdrop','other'}:
            raise ValueError('unknown entitlement kind')
        for key in ['project_id','position_id','benefit_id','conditions']:
            if not isinstance(value[key],str) or not value[key].strip():raise ValueError('explicit entitlement description required')
        identity={k:value[k] for k in ['project_id','chain_id','position_id','benefit_id']}
        identity.update(wallet=address(value['wallet']),contract=address(value['contract']))
        key=digest(dict(owner=owner,identity=identity))
        interval=value.get('check_interval_seconds',3600)
        if type(interval) is not int or interval<60:raise ValueError('claim check interval must be at least 60 seconds')
        record=dict(id=key,**identity,kind=value['kind'],recurring=value['recurring'],conditions=value['conditions'],
            deadline=stamp(value.get('deadline')),next_check_at=stamp(value.get('next_check_at')),
            check_interval_seconds=interval,status='watching',observation=None,review=review)
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO claim_entitlements VALUES (?,?,?)',(key,owner,json.dumps(record)))
        return self.get(owner,key)  # Re-discovery never resets history or a completed right.
    def get(self,owner,key):
        row=self.db.execute('SELECT record FROM claim_entitlements WHERE owner=? AND id=?',(owner,key)).fetchone()
        if not row:raise ValueError('entitlement not owned by this installation')
        return json.loads(row[0])
    def _bind(self,record,fact):
        evidence(fact)
        if (fact.get('entitlement_id')!=record['id'] or fact.get('chain_id')!=record['chain_id']
            or address(fact.get('wallet'))!=record['wallet'] or address(fact.get('contract'))!=record['contract']):
            raise ValueError('entitlement evidence binding mismatch')
    def observe(self,owner,key,fact):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE');record=self.get(owner,key);self._bind(record,fact)
            state=fact.get('state')
            if state not in {'claimable','waiting','ended','ineligible','unknown'}:raise ValueError('invalid entitlement state')
            observed=stamp(fact.get('observed_at'));valid=stamp(fact.get('valid_until'));now=self.clock()
            if observed is None or valid is None or observed>now+5 or valid<=now or valid<observed:
                raise ValueError('fresh timestamped claim observation required')
            previous=record.get('observation')
            if previous and observed<previous['observed_at']:raise ValueError('older entitlement observation')
            sequence=fact.get('period_sequence')
            prior_sequence=record.get('period_sequence')
            if sequence is not None and (type(sequence) is not int or sequence<0):raise ValueError('period sequence must be nonnegative integer')
            if prior_sequence is not None and (sequence is None or sequence<prior_sequence):raise ValueError('period sequence cannot regress')
            if sequence is not None:
                if prior_sequence is not None and sequence>prior_sequence and 'deadline' not in fact:
                    raise ValueError('new period must explicitly provide deadline or null')
                record['period_sequence']=sequence
            if record['status']=='completed':return record
            if 'amount_raw' in fact:uint(fact['amount_raw'])
            if 'deadline' in fact:record['deadline']=stamp(fact['deadline'])
            record.update(status=state,observation=fact,next_check_at=stamp(fact.get('next_check_at',now+record['check_interval_seconds'])))
            self.db.execute('UPDATE claim_entitlements SET record=? WHERE id=?',(json.dumps(record),key))
        return record
    @staticmethod
    def normalize_fact(fact):
        result={**fact,'wallet':address(fact.get('wallet')),'contract':address(fact.get('contract'))}
        if result.get('tx_hash'):result['tx_hash']=result['tx_hash'].lower()
        if 'received_assets' in result:
            if not isinstance(result['received_assets'],list):raise ValueError('received assets must be a list')
            assets=[]
            for value in result['received_assets']:
                if not isinstance(value,dict):raise ValueError('invalid received asset')
                asset={**value,'recipient':address(value.get('recipient')),
                       'asset':'native' if value.get('asset')=='native' else address(value.get('asset')),
                       'amount_raw':uint(value.get('amount_raw'))}
                assets.append(asset)
            result['received_assets']=sorted(assets,key=lambda a:json.dumps(a,sort_keys=True))
        return result

    def review_claim(self,owner,key,attempt_id,fact,review):
        if (review.get('approved') is not True or not all(review.get(k) for k in ['reviewer','evidence_ref','reason','expected_digest'])):
            raise PermissionError('independent claim correction review required')
        if fact.get('status') not in {'confirmed','reverted'}:raise ValueError('correction must be a finalized claim')
        return self.record_claim(owner,key,attempt_id,fact,_review=review)

    def events(self,owner,attempt_id=None):
        rows=self.db.execute('SELECT id,record FROM claim_events WHERE owner=? ORDER BY rowid',(owner,))
        return [dict(event_id=i,**json.loads(raw)) for i,raw in rows if attempt_id is None or json.loads(raw)['attempt_id']==attempt_id]

    def record_claim(self,owner,key,attempt_id,fact,*,_review=None):
        if not isinstance(attempt_id,str) or not attempt_id:raise ValueError('stable claim attempt id required')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE');record=self.get(owner,key);self._bind(record,fact)
            if stamp(fact.get('observed_at')) is None:raise ValueError('claim observation time required')
            status=fact.get('status')
            if status not in {'submitted','pending','unknown','confirmed','reverted'}:raise ValueError('invalid claim status')
            tx=fact.get('tx_hash')
            if tx is not None and not re.fullmatch(r'0x[0-9a-fA-F]{64}',tx):raise ValueError('invalid claim transaction hash')
            if tx:tx=tx.lower()
            if status in {'confirmed','reverted'} and (not tx or fact.get('finalized') is not True):
                raise ValueError('finalized receipt required before marking claim terminal')
            if status=='confirmed':
                assets=fact.get('received_assets')
                if not isinstance(assets,list) or not assets:raise ValueError('actual received assets required')
                for asset in assets:
                    if address(asset.get('recipient'))!=record['wallet']:raise ValueError('claim recipient differs from tracked wallet')
                    if asset.get('asset')!='native':address(asset.get('asset'))
                    uint(asset.get('amount_raw'))
            normalized=self.normalize_fact({**fact,'tx_hash':tx});event=dict(entitlement_id=key,attempt_id=attempt_id,fact=normalized)
            old=self.db.execute('SELECT entitlement,record FROM claim_attempts WHERE owner=? AND id=?',(owner,attempt_id)).fetchone()
            if old:
                raw_previous=json.loads(old[1]);previous=self.normalize_fact(raw_previous)
                if _review and _review['expected_digest']!=digest(raw_previous):
                    raise ValueError('claim changed since correction review')
                if old[0]!=key or (previous.get('tx_hash') and previous['tx_hash']!=tx):raise ValueError('claim attempt identity changed')
                if previous['status'] in {'confirmed','reverted'}:
                    if status not in {'confirmed','reverted'}:return previous
                    fields=['entitlement_id','chain_id','wallet','contract','status','tx_hash','received_assets']
                    differs=any(previous.get(k)!=normalized.get(k) for k in fields)
                    if differs and not _review:raise ValueError('terminal claim conflict requires explicit review')
                    if not differs:return previous
            if _review:
                if not old or previous['status'] not in {'confirmed','reverted'}:
                    raise ValueError('correction requires existing terminal claim')
                event.update(previous=raw_previous,review=_review)
                self.db.execute('INSERT INTO claim_reviews VALUES (?,?,?,?)',(digest(event),owner,attempt_id,json.dumps(event)))
            if tx:
                for existing_id,raw in self.db.execute('SELECT id,record FROM claim_attempts WHERE owner=? AND entitlement=?',(owner,key)):
                    if existing_id!=attempt_id and json.loads(raw).get('tx_hash')==tx:
                        raise ValueError('transaction already tracked under another attempt id')
            self.db.execute('INSERT OR REPLACE INTO claim_attempts VALUES (?,?,?,?)',(owner,attempt_id,key,json.dumps(normalized)))
            self.db.execute('INSERT OR IGNORE INTO claim_events VALUES (?,?,?)',(digest(dict(owner=owner,event=event)),owner,json.dumps(event)))
            has_confirmed=any(json.loads(raw)['status']=='confirmed' for raw, in self.db.execute('SELECT record FROM claim_attempts WHERE owner=? AND entitlement=?',(owner,key)))
            record.update(status='completed' if has_confirmed and not record['recurring'] else 'watching',
                observation=None,next_check_at=stamp(fact.get('next_check_at',self.clock()+record['check_interval_seconds'])))
            self.db.execute('UPDATE claim_entitlements SET record=? WHERE id=?',(json.dumps(record),key))
        return normalized
    def history(self,owner,key=None):
        if key:self.get(owner,key)
        rows=self.db.execute('SELECT id,entitlement,record FROM claim_attempts WHERE owner=? ORDER BY rowid',(owner,))
        return [{**json.loads(r),'attempt_id':i,'entitlement_id':e,'record_digest':digest(json.loads(r))} for i,e,r in rows if key is None or key==e]
    def dashboard(self,owner):
        now=self.clock();result=[];history=self.history(owner)
        for key,raw in self.db.execute('SELECT id,record FROM claim_entitlements WHERE owner=? ORDER BY rowid',(owner,)):
            record=json.loads(raw);observation=record.get('observation')
            if observation and observation['valid_until']<=now:record['status']='needs_refresh'
            unresolved=[x['attempt_id'] for x in history if x['entitlement_id']==key and x['status'] in {'submitted','pending','unknown'}]
            confirmed=[x for x in history if x['entitlement_id']==key and x['status']=='confirmed']
            record.update(confirmed_claims=len(confirmed),last_claim_observed_at=max((x['observed_at'] for x in confirmed),default=None),unresolved_attempts=unresolved,can_offer_claim=record['status']=='claimable' and not unresolved and (record['deadline'] is None or record['deadline']>now),
                check_due=record['status']!='completed' and (record['next_check_at'] is None or record['next_check_at']<=now or (observation is not None and observation['valid_until']<=now)),
                deadline_passed=record['deadline'] is not None and record['deadline']<=now)
            result.append(record)
        scans=[dict(order_id=i,**json.loads(raw)) for i,raw in self.db.execute('SELECT order_id,record FROM claim_scans WHERE owner=?',(owner,))]
        return dict(entitlements=result,unreviewed_projects=[s for s in scans if s['state']!='reviewed'],history=history)
    def scan_order(self,owner,order,host):
        if order['state'] not in {'mint_verified','complete'} or order.get('result',{}).get('finalized') is not True:return
        row=self.db.execute('SELECT record FROM claim_scans WHERE owner=? AND order_id=?',(owner,order['id'])).fetchone()
        if row:
            previous=json.loads(row[0])
            if previous['state']=='reviewed' or previous.get('retry_after',0)>self.clock():return
        pending=dict(state='needs_review',project_id=order['intent']['project_id'],chain_id=order['intent']['chain_id'],wallet=order['intent']['wallet'])
        with self.db:self.db.execute('INSERT OR IGNORE INTO claim_scans VALUES (?,?,?)',(owner,order['id'],json.dumps(pending)))
        discover=getattr(host,'entitlements_for_order',None)
        if not callable(discover):return  # Visible project-level debt, never silently forget.
        try:
            findings=discover(order);evidence(findings)
            if not findings.get('reviewer'):raise PermissionError('entitlement discovery reviewer required')
            if findings.get('order_id')!=order['id'] or not isinstance(findings.get('entitlements'),list):raise ValueError('invalid entitlement discovery')
            for value in findings['entitlements']:
                if value['chain_id']!=order['intent']['chain_id'] or address(value['wallet'])!=order['intent']['wallet'] or value['project_id']!=order['intent']['project_id']:
                    raise ValueError('discovered entitlement outside order scope')
                self.register(owner,value,findings)
        except Exception as exc:
            pending['last_error']=type(exc).__name__;pending['retry_after']=self.clock()+300
            with self.db:self.db.execute('UPDATE claim_scans SET record=? WHERE owner=? AND order_id=?',(json.dumps(pending),owner,order['id']))
            return
        with self.db:self.db.execute('UPDATE claim_scans SET record=? WHERE owner=? AND order_id=?',(json.dumps({**pending,'state':'reviewed','evidence_ref':findings['evidence_ref']}),owner,order['id']))
