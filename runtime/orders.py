"""Private, revisioned mint orders. Host callbacks establish facts/approval.

Records are per installation, never a distributed case corpus. A transaction
receipt, an asset receipt and final cleanup are separate facts.
"""
import json
import math
import sqlite3
import time
import uuid
from .common import address, digest, transaction, uint, AgentPending
from .quantity import normalize_quantity

LOCKED = {'dispatching', 'submission_unknown', 'submitted', 'mint_verified', 'failed', 'complete', 'cancelled'}
IDENTITY = ('project_id', 'activity_id', 'chain_id', 'wallet', 'recipient')


def split_intent(raw, quantity_policy='fill_to_max'):
    required = set(IDENTITY)
    if not required <= set(raw) <= required | {'quantity'} or type(raw['chain_id']) is not int or raw['chain_id'] <= 0:
        raise ValueError('explicit project/activity/chain/wallet/recipient required')
    if not raw['project_id'] or not raw['activity_id']:
        raise ValueError('project and activity required')
    quantity, mode = normalize_quantity(raw, quantity_policy)
    body = {key: raw[key] for key in IDENTITY}
    body.update(wallet=address(raw['wallet']), recipient=address(raw['recipient']), quantity=quantity)
    return body, mode


def intent(raw, quantity_policy='fill_to_max'):
    """Six-field order intent. Omitted quantity stays null until fill-to-max resolution."""
    return split_intent(raw, quantity_policy)[0]


def order_intent_key(body, mode):
    # Explicit keys stay digest(intent) so existing orders still dedupe.
    if mode == 'fill_to_max':
        return digest({**body, 'quantity_mode': mode})
    return digest(body)


def validate_plan(order, plan):
    tx = transaction(plan['transaction'])
    wanted = order['intent']
    if plan.get('intent_digest') != digest(wanted):
        raise ValueError('plan belongs to another order intent')
    if tx['chain_id'] != wanted['chain_id'] or tx['wallet'] != wanted['wallet']:
        raise ValueError('transaction wallet/chain changed')
    if not plan.get('audit_ref') or not plan.get('preview_request_id'):
        raise ValueError('reviewed audit and actual wallet preview required')
    if type(plan.get('expires_at')) is not int:
        raise ValueError('explicit shortest quote/proof/preview expiry required')
    binding = plan.get('static_binding', {})
    if not {'runtime_sha256', 'implementation_sha256', 'permissions', 'payment_path', 'dependencies'} <= binding.keys():
        raise ValueError('static audit binding incomplete')
    if not isinstance(plan.get('dynamic_binding'), dict) or not plan['dynamic_binding']:
        raise ValueError('current eligibility/price/quantity/proof state required')
    assets=plan.get('expected_assets')
    if not isinstance(assets,list) or not assets or any(not isinstance(a,dict) for a in assets):
        raise ValueError('explicit expected asset entries required')
    normalized_assets=[(address(a.get('recipient')),uint(a.get('quantity'))) for a in assets]
    if (wanted['recipient'],wanted['quantity']) not in normalized_assets:
        raise ValueError('expected output must bind intended recipient and quantity')
    if not isinstance(plan.get('risks'), list):
        raise ValueError('expected output and risk disclosure required')
    for key in ['approvals', 'followups']:
        if not isinstance(plan.get(key), list):
            raise ValueError('explicit approval and followup lists required')
    ids = [f['id'] for f in plan['followups']]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate followup id')
    for task in plan['followups']:
        for field in ['not_before','poll_interval_seconds']:
            v=task.get(field,0 if field=='not_before' else 60)
            if type(v) not in {int,float} or not math.isfinite(v) or v<0 or (field=='poll_interval_seconds' and v<1):
                raise ValueError('invalid followup check schedule')
    totals = {}
    kinds = set()
    for cost in plan.get('costs', []):
        if cost.get('kind') not in {'payment', 'mint_gas', 'cleanup_gas', 'followup_gas', 'service'}:
            raise ValueError('invalid cost kind')
        asset = cost['asset'] if cost['asset'] == 'native' else address(cost['asset'])
        if cost['kind'].endswith('_gas') and asset!='native':
            raise ValueError('gas reserve must use native asset')
        amount = int(uint(cost['amount_raw']))
        totals[asset] = totals.get(asset, 0) + amount
        kinds.add(cost['kind'])
    if not {'payment', 'mint_gas', 'cleanup_gas', 'followup_gas'} <= kinds:
        raise ValueError('payment, mint gas, cleanup gas and followup reserve must be explicit (zero allowed)')
    native_payment = sum(int(c['amount_raw']) for c in plan['costs'] if c['kind']=='payment' and c['asset']=='native')
    if native_payment != int(tx['value_wei']):
        raise ValueError('native payment differs from transaction value')
    balances = plan.get('balances', {})
    if any(int(uint(balances.get(asset, 'invalid'))) < amount for asset, amount in totals.items()):
        raise ValueError('balance cannot cover payment plus cleanup/followup reserves')
    return totals


class OrderStore:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS mint_orders(id TEXT PRIMARY KEY, owner TEXT, intent_key TEXT, request_key TEXT, revision INTEGER, record TEXT);
          CREATE TABLE IF NOT EXISTS mint_request_aliases(owner TEXT, request_key TEXT, order_id TEXT, PRIMARY KEY(owner,request_key));
          CREATE UNIQUE INDEX IF NOT EXISTS order_requests ON mint_orders(owner,request_key);
        ''')

    def close(self): self.db.close()

    def create(self, owner, request_key, raw_intent, *, explicit_repeat=False, quantity_policy='fill_to_max'):
        if not owner or not request_key:
            raise ValueError('installation owner and stable incoming request key required')
        wanted, mode = split_intent(raw_intent, quantity_policy); key = order_intent_key(wanted, mode)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT o.record FROM mint_orders o JOIN mint_request_aliases a ON o.id=a.order_id WHERE a.owner=? AND a.request_key=?', (owner,request_key)).fetchone()
            if row is None:
                row = self.db.execute('SELECT record FROM mint_orders WHERE owner=? AND request_key=?', (owner,request_key)).fetchone()
            if row:
                existing=json.loads(row[0])
                if not self._request_matches(existing, wanted, mode):
                    raise ValueError('same request id changed intent')
                return existing
            previous=self._previous_order(owner, wanted, mode, key)
            if previous:
                repeatable = previous['state'] in {'complete','cancelled'} or (previous['state']=='failed' and previous['cleanup'].get('state')=='clean' and previous.get('result',{}).get('finalized') is True)
                if not explicit_repeat or not repeatable:
                    self.db.execute('INSERT INTO mint_request_aliases VALUES (?,?,?)',(owner,request_key,previous['id']))
                    return previous  # A reminder/retry never creates another spend.
            order=dict(id=str(uuid.uuid4()),owner=owner,intent=wanted,quantity_mode=mode,state='preparing',revision=1,
                       plan=None,confirmation=None,submission=None,cleanup={'state':'not_started'},followups=[])
            self.db.execute('INSERT INTO mint_orders VALUES (?,?,?,?,?,?)',
                            (order['id'],owner,key,request_key,1,json.dumps(order)))
            self.db.execute('INSERT INTO mint_request_aliases VALUES (?,?,?)',(owner,request_key,order['id']))
            return order

    def _request_matches(self, existing, wanted, mode):
        current=existing['intent']
        if mode=='fill_to_max' and existing.get('quantity_mode','explicit')=='fill_to_max':
            return all(current.get(key)==wanted[key] for key in IDENTITY)
        return current==wanted

    def _previous_order(self, owner, wanted, mode, key):
        if mode=='fill_to_max':
            rows=self.db.execute('SELECT record FROM mint_orders WHERE owner=? ORDER BY rowid DESC',(owner,)).fetchall()
            for raw, in rows:
                order=json.loads(raw)
                if order.get('quantity_mode')=='fill_to_max' and all(order['intent'].get(key)==wanted[key] for key in IDENTITY):
                    return order
            return None
        row=self.db.execute('SELECT record FROM mint_orders WHERE owner=? AND intent_key=? ORDER BY rowid DESC',(owner,key)).fetchone()
        return json.loads(row[0]) if row else None

    def bind_quantity(self, owner, order_id, quantity):
        quantity=uint(quantity)
        if int(quantity)<=0: raise ValueError('positive resolved quantity required')
        order=self.get(owner, order_id)
        if order['state'] in LOCKED:
            raise ValueError('cannot change quantity on an in-flight or finished order')
        if order['intent'].get('quantity')==quantity:
            return order
        order['intent']={**order['intent'], 'quantity': quantity}
        order.update(confirmation=None, plan=None, state='preparing', followups=[])
        order.pop('plan_review', None)
        key=order_intent_key(order['intent'], order.get('quantity_mode','explicit'))
        old=order['revision']; order['revision']+=1
        with self.db:
            changed=self.db.execute('UPDATE mint_orders SET intent_key=?, revision=?, record=? WHERE id=? AND owner=? AND revision=?',
                                    (key, order['revision'], json.dumps(order), order['id'], order['owner'], old)).rowcount
            if changed!=1: raise RuntimeError('order changed concurrently; reload')
        return order

    def mark_needs_refresh(self, owner, order_id):
        order=self.get(owner, order_id)
        if order['state'] in LOCKED:
            raise ValueError('cannot refresh an in-flight or finished order')
        if order['state']=='needs_refresh' and order.get('confirmation') is None:
            return order
        order.update(state='needs_refresh', confirmation=None)
        return self._save(order)

    def get(self, owner, order_id):
        row=self.db.execute('SELECT record FROM mint_orders WHERE owner=? AND id=?',(owner,order_id)).fetchone()
        if not row: raise ValueError('order not owned by this installation')
        return json.loads(row[0])

    def _save(self, order):
        old=order['revision']; order['revision']+=1
        with self.db:
            changed=self.db.execute('UPDATE mint_orders SET revision=?,record=? WHERE id=? AND owner=? AND revision=?',
                                    (order['revision'],json.dumps(order),order['id'],order['owner'],old)).rowcount
            if changed!=1: raise RuntimeError('order changed concurrently; reload')
        return order

    def prepare(self, owner, order_id, plan, host):
        order=self.get(owner,order_id)
        if order['state'] in LOCKED: raise ValueError('cannot replace an in-flight or finished plan')
        totals=validate_plan(order,plan)
        if plan['expires_at']<=time.time(): raise ValueError('plan already expired')
        if host.verify_plan(plan,plan['approvals']) is not True:
            raise PermissionError('host review of audit, exact ABI/calldata, recipient and outputs required')
        order.update(plan=plan,confirmation=None,state='awaiting_confirmation',
                     plan_review=dict(plan_digest=digest(plan),cost_totals=totals,checked_at=time.time()),
                     followups=[{**f,'state':'pending'} for f in plan['followups']])
        return self._save(order)

    def confirm(self, owner, order_id, host):
        order=self.get(owner,order_id)
        if order['state']!='awaiting_confirmation': raise ValueError('not awaiting confirmation')
        self.reviewed_costs(order)
        plan=order['plan']; bound=digest(plan)
        if plan['expires_at']<=time.time(): raise ValueError('refresh expired plan before confirmation')
        # Must display the mint order and original wallet warnings/preview.
        if host.confirm_order(order,bound) is not True: raise PermissionError('order confirmation required')
        order.update(confirmation=bound,state='ready')
        return self._save(order)

    def reviewed_costs(self,order):
        review=order.get('plan_review',{})
        if review.get('plan_digest')!=digest(order['plan']):
            raise PermissionError('plan lacks matching full review; prepare again')
        return review['cost_totals']

    def pre_send(self, owner, order_id, current, now=None):
        order=self.get(owner,order_id);plan=order['plan'];now=time.time() if now is None else now
        if order['state']!='ready' or order['confirmation']!=digest(plan):
            raise PermissionError('matching confirmed order required')
        totals=self.reviewed_costs(order)
        # current is independent host/RPC evidence, never agent self-certification.
        if current.get('verified') is not True or not current.get('evidence_ref'):
            raise PermissionError('independent current checks required')
        if current.get('plan_digest')!=digest(plan) or current.get('policy_valid') is not True:
            raise PermissionError('fresh snapshot must bind this plan and current wallet policy')
        observed=current.get('observed_at');valid=current.get('valid_until')
        if type(observed) not in {int,float} or type(valid) not in {int,float} or not math.isfinite(observed) or not math.isfinite(valid) or observed>now+5:
            raise PermissionError('timestamped current evidence required')
        if valid<observed or now>=valid:
            order.update(state='needs_refresh',confirmation=None)
            return self._save(order)
        if digest(current.get('static_binding'))!=digest(plan['static_binding']):
            order.update(state='needs_reaudit',confirmation=None)
            return self._save(order)
        if (now>=plan['expires_at'] or digest(current.get('dynamic_binding'))!=digest(plan['dynamic_binding'])
                or current.get('preview_valid') is not True):
            order.update(state='needs_refresh',confirmation=None)
            return self._save(order)
        # Current balances are checked again, including revocation reserves.
        balances=current.get('balances',{})
        if any(int(uint(balances.get(asset,'invalid')))<amount for asset,amount in totals.items()):
            raise ValueError('fresh balance cannot cover reviewed costs and reserves')
        order['state']='dispatching'
        order['transport_started']=False
        return self._save(order)  # Durable before entering a potentially uncertain write.

    def invalidate_audits(self,owner,case_ids):
        changed=[]
        records=self.db.execute('SELECT record FROM mint_orders WHERE owner=?',(owner,)).fetchall()
        for raw, in records:
            order=json.loads(raw)
            if order['state'] in LOCKED or not order['plan'] or order['plan']['audit_ref'] not in case_ids:continue
            order.update(state='needs_reaudit',confirmation=None)
            try:self._save(order)
            except RuntimeError:continue  # Concurrent submit must reconcile, never release its lock.
            changed.append(order['id'])
        return changed

    def cancel(self, owner, order_id):
        order=self.get(owner,order_id)
        if order['state'] in LOCKED:
            raise ValueError('in-flight orders require independent reconciliation, not local cancellation')
        order.update(state='cancelled',confirmation=None)
        return self._save(order)

    def mark_transport_started(self,owner,order_id):
        order=self.get(owner,order_id)
        if order['state']!='dispatching':raise ValueError('order not dispatching')
        order['transport_started']=True
        return self._save(order)

    def submitted(self, owner, order_id, response):
        order=self.get(owner,order_id)
        if order['state']!='dispatching': raise ValueError('order not dispatching')
        order.update(state='submitted',submission=response)
        return self._save(order)

    def pre_dispatch_failed(self,owner,order_id):
        order=self.get(owner,order_id)
        if order['state']!='dispatching':raise ValueError('order not dispatching')
        order.update(state='awaiting_confirmation',confirmation=None)
        return self._save(order)

    def unknown(self, owner, order_id):
        order=self.get(owner,order_id)
        if order['state']!='dispatching': raise ValueError('order not dispatching')
        order['state']='submission_unknown';return self._save(order)

    def reconcile(self, owner, order_id, host):
        order=self.get(owner,order_id)
        if order['state'] not in LOCKED or order['state'] in {'complete','cancelled','dispatching'}: return order
        if (order['state'] in {'mint_verified','failed'} and order.get('result',{}).get('finalized') is True
                and order['result'].get('plan_digest')==digest(order['plan'])):
            return order
        result=host.verify_order_result(order)
        if result.get('verified') is not True or not result.get('evidence_ref'):
            return order
        # Host verifies intended assets/recipient/quantity, not just receipt.status.
        state=result.get('state')
        if state not in {'pending','unknown','mint_verified','reverted','aborted_no_pending'}:
            raise ValueError('invalid verified order result')
        if state in {'unknown','pending'}:
            return order
        order['result']={**result,'order_id':order_id,'plan_digest':digest(order['plan']),
                         'chain_id':order['intent']['chain_id'],'wallet':order['intent']['wallet']}
        order['state']='mint_verified' if state=='mint_verified' else 'failed'
        return self._save(order)

    def finish(self, owner, order_id, cleanup, host, *, now=None):
        now=time.time() if now is None else now
        order=self.get(owner,order_id)
        if order['state'] not in {'mint_verified','failed','complete'}: return order
        if order['state']=='complete':return order
        # Persist a changed cleanup result before potentially fallible reads.
        if order['cleanup']!=cleanup:
            order['cleanup']=cleanup
            order=self._save(order)
        due_tasks=[task for task in order['followups'] if task['state']=='pending'
                   and now>=max(task.get('not_before',0),task.get('next_check_at',0))]
        can_complete=(cleanup.get('state')=='clean' and order['state']=='mint_verified'
            and order.get('result',{}).get('finalized') is True
            and all(t['state']!='pending' for t in order['followups']))
        if not due_tasks:
            if can_complete:
                order['state']='complete';return self._save(order)
            return order
        for task in order['followups']:
            due=max(task.get('not_before',0),task.get('next_check_at',0))
            if task['state']!='pending' or now<due:continue
            task['last_check_at']=now
            task['next_check_at']=now+task.get('poll_interval_seconds',60)
            try:
                fact=host.verify_followup(order,task)
            except (AgentPending,TimeoutError,ConnectionError,PermissionError) as exc:
                task['last_error']=type(exc).__name__
                if isinstance(exc,AgentPending):task['analysis_task_id']=exc.task_id
                continue
            suggested=fact.get('next_check_at')
            if (fact.get('verified') is True and fact.get('evidence_ref')
                and type(suggested) in {int,float} and math.isfinite(suggested)
                and suggested>task['next_check_at']):task['next_check_at']=suggested
            if fact.get('verified') is True and fact.get('evidence_ref') and fact.get('state') in {'done','not_applicable'}:
                recorder=getattr(host,'record_claim_followup',None)
                try:
                    if recorder:recorder(order,task,fact)
                except (ValueError,PermissionError) as exc:
                    task['last_error']=type(exc).__name__
                    continue
                task.update(state=fact['state'],evidence_ref=fact['evidence_ref'])
                task.pop('last_error',None)
        if cleanup.get('state')=='clean' and all(t['state']!='pending' for t in order['followups']):
            if order['state']=='mint_verified' and order.get('result',{}).get('finalized') is True:order['state']='complete'
        return self._save(order)

    def card(self, owner, order_id):
        order=self.get(owner,order_id);p=order['plan'] or {}
        return dict(order_id=order_id,state=order['state'],quantity_mode=order.get('quantity_mode','explicit'),**order['intent'],
                    costs=p.get('costs'),expected_assets=p.get('expected_assets'),risks=p.get('risks'),
                    cleanup=order['cleanup'],followups=order['followups'],finalized=order.get('result',{}).get('finalized') is True)
