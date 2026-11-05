"""Direct tools for standard work; agents only fill explicit semantic gaps."""
from .common import digest, NeedsAgent, AgentPending


class DelegatingHost:
    def __init__(self, host, router, wallet_queue=None, claims=None, owner=None):
        self.host,self.router=host,router
        self.wallet_queue=wallet_queue
        self.claims,self.owner=claims,owner
    def __getattr__(self,name):return getattr(self.host,name)
    def _read(self,role,payload,question):
        payload={**payload,'question':question}
        # Same evidence/question reuses the task across polling and restarts.
        # A new observed block/version yields a new task; never cache live facts.
        task_id='analysis:'+role+':'+digest(payload)
        response=self.router.call(task_id,role,payload)
        if response['status']=='pending':raise AgentPending(task_id,role)
        if response['status']!='completed':raise RuntimeError('delegated analysis did not complete: '+response['status'])
        return response['data']
    def build_plan(self,order,snapshot,template,checks):
        try:
            return self.host.finalize_plan(order,snapshot,template,checks,None)
        except NeedsAgent as gap:
            proposal=self._read('trade.prepare',dict(intent=order['intent'],snapshot=snapshot,
                template=template,learned_checks=checks),gap.question)
        return self.host.finalize_plan(order,snapshot,template,checks,proposal)
    def verify_order_result(self,order):
        try:return self.host.verify_order_result(order)
        except NeedsAgent as gap:
            evidence=self._read('receipt.inspect',dict(order_id=order['id'],intent=order['intent'],
                submission=order['submission'],expected_assets=order['plan']['expected_assets']),gap.question)
        return self.host.verify_order_evidence(order,evidence)
    def verify_followup(self,order,task):
        try:return self.host.verify_followup(order,task)
        except NeedsAgent as gap:
            evidence=self._read('followup.plan',dict(intent=order['intent'],task={k:v for k,v in task.items()
                if k not in {'last_check_at','next_check_at','last_error','analysis_task_id'}}),gap.question)
        return self.host.verify_followup_evidence(order,task,evidence)
    def has_authority(self,approval):
        try:return self.host.has_authority(approval)
        except NeedsAgent as gap:
            evidence=self._read('approval.inspect',dict(approval=approval),gap.question)
        return self.host.verify_authority_evidence(approval,evidence)
    def revoke(self,approval,idempotency_key):
        direct=getattr(self.host,'revoke',None)
        if callable(direct):
            # Never fall back after a write callback raises: it may have sent.
            return direct(approval,idempotency_key=idempotency_key)
        return self.router.call(idempotency_key,'approval.revoke',dict(approval=approval),authorize=self.host.authorize)
    def in_use_elsewhere(self,approval,workflow_id):
        if self.wallet_queue and self.wallet_queue.busy(approval['chain_id'],approval['wallet'],workflow_id):return True
        return self.host.in_use_elsewhere(approval,workflow_id)

    def inspect_entitlement(self,entry):
        try:return self.host.inspect_entitlement(entry)
        except NeedsAgent as gap:
            payload={k:entry[k] for k in ['id','project_id','chain_id','wallet','contract','position_id','benefit_id','kind','conditions']}
            evidence=self._read('followup.plan',dict(entitlement=payload),gap.question)
        return self.host.verify_entitlement_evidence(entry,evidence)
    def record_claim_followup(self,order,task,fact):
        event=fact.get('claim_event')
        if self.claims is not None and event is not None:
            if not isinstance(event,dict) or any(not isinstance(event.get(k),str) or not event[k] for k in ['entitlement_id','attempt_id']):
                raise ValueError('claim_event requires stable entitlement/attempt id')
            entry=self.claims.get(self.owner,event['entitlement_id'])
            if (entry['wallet']!=order['intent']['wallet'] or entry['chain_id']!=order['intent']['chain_id']
                or entry['project_id']!=order['intent']['project_id']):
                raise ValueError('claim event outside order scope')
            self.claims.record_claim(self.owner,entry['id'],event['attempt_id'],event)

    def track_finalized_entitlements(self,order):
        if self.claims is not None:self.claims.scan_order(self.owner,order,self.host)
