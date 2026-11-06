"""Product entry point; trusted host supplies wallet UI and independent evidence."""
from .common import digest, address, PreDispatchRejected
from .quantity import resolve_mint_quantity
from .research_method import require_target


class MintCoordinator:
    def __init__(self, orders, templates, workflow, wallet_queue=None, limits=None):
        self.orders,self.templates,self.workflow=orders,templates,workflow
        self.wallet_queue=wallet_queue
        self.limits=limits or {}

    def _bind_case(self,plan):
        record=self.workflow.cases.require_case(plan['audit_ref'])
        if not record:raise PermissionError('reviewed audit snapshot required')
        snapshot=record['snapshot']
        expected={k:snapshot[k] for k in ['runtime_sha256','implementation_sha256']}
        expected.update({k:snapshot['context'][k] for k in ['permissions','payment_path','dependencies']})
        tx=plan['transaction']
        if tx['chain_id']!=snapshot['chain_id'] or address(tx['to'])!=address(snapshot['address']):
            raise PermissionError('transaction target differs from reviewed contract')
        selectors=record['audit'].get('mint_selectors',[])
        data=tx.get('data','')
        selector=data[2:10].lower() if isinstance(data,str) and data.startswith('0x') and len(data)>=10 else None
        if not selectors or selector not in selectors or selector not in snapshot['selectors']:
            raise PermissionError('calldata selector is not explicitly audited for mint')
        if digest(plan.get('static_binding'))!=digest(expected):
            raise PermissionError('plan static binding differs from reviewed contract')

    def prepare(self, owner, order_id, snapshot, resolution, evidence, host, features=None):
        order=self.orders.get(owner,order_id)
        if order['state'] not in {'preparing','awaiting_confirmation','ready','needs_refresh','needs_reaudit'}:
            raise ValueError('cannot prepare an in-flight order')
        require_target(snapshot,resolution)
        if snapshot['chain_id']!=order['intent']['chain_id']:
            raise ValueError('selected chain differs from research')
        match=self.workflow.cases.match(snapshot)
        if match['route']=='contract_blocked':return dict(state='contract_blocked')
        template=self.templates.find(snapshot,match)
        checks=self.workflow.evolution.checks(features or {}) if self.workflow.evolution else []
        if template is None:
            method_matches={};source=None
            if self.workflow.patterns and hasattr(host,'pattern_source'):
                source=host.pattern_source(snapshot)
                if source and source.get('verified') is True:
                    method_matches=self.workflow.patterns.match(source['source'])
                else:source=None
            payload=dict(snapshot=snapshot,evidence=evidence,case_match=match,learned_checks=checks,method_matches=method_matches,
                         target_resolution=resolution,source_material=source,
                         task='Reuse supplied verified evidence and resolved target; fetch only missing or stale facts. Apply original read-only research method. Outer builder returns an unbound recipe including followup_checks, never wallet execution.')
            response=self.workflow.router.call(order_id+':audit.prepare:'+digest(payload),'audit.prepare',payload)
            if response['status']!='completed':return dict(state='research_pending',response=response)
            data=response.get('data',{})
            if not isinstance(data.get('audit'),dict) or not isinstance(data.get('recipe'),dict):
                return dict(state='research_failed',reason='invalid_audit_prepare_response')
            audit=data['audit']
            case_id=self.workflow.learn(snapshot,audit,host,source_material=source)
            if audit['verdict']!='pass' or self.workflow.cases.match(snapshot)['route']!='reuse_static_analysis':
                return dict(state='audit_blocked',case_id=case_id)
            template=self.templates.record(snapshot,data['recipe'],case_id,host)
        # Quantity is concrete before the host encodes calldata. Fill-to-max uses
        # current caps; explicit requests that no longer fit are rejected here.
        order=self.sync_quantity(owner, order, snapshot, host, rewrite=True)
        # Builder fetches wallet-specific proofs/signatures and performs current
        # simulation/preview. It must verify every active learned check locally.
        plan=host.build_plan(order,snapshot,template,checks)
        if plan.get('audit_ref')!=template['case_id']:
            raise ValueError('plan lost reviewed case binding')
        self._bind_case(plan)
        required={f['id'] for f in template['recipe'].get('followup_checks',[])}
        if not required<={f['id'] for f in plan.get('followups',[])}:
            raise ValueError('prepared plan omitted audited followup tasks')
        return self.orders.prepare(owner,order_id,plan,host)

    def _resolved_quantity(self, order, snapshot, host):
        reader=getattr(host,'mint_caps',None)
        if not callable(reader):
            raise PermissionError('host mint_caps required to enforce wallet and per-tx caps')
        caps=reader(order, snapshot)
        return resolve_mint_quantity(order['intent'], caps, self.limits.get('per_transaction_shares'), order.get('quantity_mode','explicit'))

    def sync_quantity(self, owner, order, snapshot, host, *, rewrite):
        """Apply fill-to-max or reject an over-cap explicit quantity before a plan is sent."""
        quantity=self._resolved_quantity(order, snapshot, host)
        if order['intent'].get('quantity')==quantity:
            return order
        if not rewrite:
            return self.orders.mark_needs_refresh(owner, order['id'])
        return self.orders.bind_quantity(owner, order['id'], quantity)

    def _hold_for_refresh(self, owner, order_id):
        order=self.orders.mark_needs_refresh(owner, order_id)
        if self.wallet_queue:self.wallet_queue.release(order_id, no_submission=True)
        return order

    def execute(self,owner,order_id,host):
        if not self.wallet_queue:return self._execute(owner,order_id,host)
        order=self.orders.get(owner,order_id)
        with self.wallet_queue.guard(order) as held:
            if not held:return dict(state='queued',order_id=order_id,reason='wallet_worker_active')
            order=self.orders.get(owner,order_id)
            self.wallet_queue.recover_pre_dispatch(order)
            return self._execute(owner,order_id,host)

    def _execute(self,owner,order_id,host):
        order=self.orders.get(owner,order_id)
        if order['state']!='ready':
            return self._reconcile(owner,order_id,host)
        try:
            order=self.sync_quantity(owner, order, None, host, rewrite=False)
        except ValueError:
            self._hold_for_refresh(owner, order_id)
            raise
        if order['state']!='ready':
            if self.wallet_queue:self.wallet_queue.release(order_id, no_submission=True)
            return order
        plan=order['plan']
        self._bind_case(plan)
        current=host.current_checks(order)
        if self.wallet_queue:
            self.wallet_queue.enqueue(order,self.orders.reviewed_costs(order))
            if not self.wallet_queue.acquire(order_id,current.get('balances',{})):
                return dict(state='queued',order_id=order_id)
        try:
            order=self.orders.pre_send(owner,order_id,current)
        except Exception:
            if self.wallet_queue:self.wallet_queue.release(order_id,no_submission=True)
            raise
        if order['state']!='dispatching':
            if self.wallet_queue:self.wallet_queue.release(order_id,no_submission=True)
            return order
        try:
            # Local validation cannot have sent a wallet transaction.
            self.workflow.cleanup.track(order_id,dict(chain_id=order['intent']['chain_id'],wallet=order['intent']['wallet']),plan['approvals'])
        except Exception:
            abandon=getattr(self.workflow.cleanup,'abandon_unsent',None)
            if abandon:abandon(order_id)
            self.orders.pre_dispatch_failed(owner,order_id)
            if self.wallet_queue:self.wallet_queue.release(order_id,no_submission=True)
            raise
        self.orders.mark_transport_started(owner,order_id)
        try:
            result=self.workflow.router.call(order_id+':trade','trade.execute',plan,authorize=lambda *args: self._authorize(plan,host,*args))
        except PreDispatchRejected:
            abandon=getattr(self.workflow.cleanup,'abandon_unsent',None)
            if abandon:abandon(order_id)
            self.orders.pre_dispatch_failed(owner,order_id)
            if self.wallet_queue:self.wallet_queue.release(order_id,no_submission=True)
            raise
        except Exception:
            self.orders.unknown(owner,order_id)
            raise  # Recover by independent queries, never a replacement write id.
        self.orders.submitted(owner,order_id,result)
        host.track_submission(order_id,plan['transaction'],result)
        return self._reconcile(owner,order_id,host)

    def _authorize(self,plan,host,*args):
        try:
            self._bind_case(plan)
            return host.authorize(*args)
        except PermissionError as exc:
            raise PreDispatchRejected(str(exc)) from exc

    def reconcile(self,owner,order_id,host):
        if not self.wallet_queue:return self._reconcile(owner,order_id,host)
        order=self.orders.get(owner,order_id)
        with self.wallet_queue.guard(order) as held:
            if not held:return order
            order=self.orders.get(owner,order_id)
            self.wallet_queue.recover_pre_dispatch(order)
            if order['state']=='dispatching' and order.get('transport_started') is False:
                self.workflow.cleanup.abandon_unsent(order_id)
                self.orders.pre_dispatch_failed(owner,order_id)
                self.wallet_queue.release(order_id,no_submission=True)
                return self.orders.get(owner,order_id)
            if order['state']=='dispatching':
                # Acquired the same sender lock: no sender is still running.
                # A crashed sender may have broadcast, so independent recovery only.
                self.orders.unknown(owner,order_id)
            return self._reconcile(owner,order_id,host)

    def _reconcile(self,owner,order_id,host):
        order=self.orders.reconcile(owner,order_id,host)
        if order['state'] in {'mint_verified','failed'}:
            fact=order['result']
            if fact.get('finalized') is not True:return order
            status={'mint_verified':'confirmed','reverted':'reverted','aborted_no_pending':'aborted'}[fact['state']]
            self.workflow.cases.record_outcome(order['plan']['audit_ref'],dict(
                status=status,evidence_ref=fact['evidence_ref'],order_id=order_id))
            cleanup=order['cleanup']
            if cleanup.get('state')!='clean':
                cleanup=self.workflow.cleanup.finish(order_id,host,terminal_evidence=fact)
            if self.wallet_queue and cleanup.get('state')=='clean':
                self.wallet_queue.release(order_id,terminal=True,cleanup_clean=True)
            tracker=getattr(host,'track_finalized_entitlements',None)
            if tracker:tracker(order)
            order=self.orders.finish(owner,order_id,cleanup,host)
        return order
