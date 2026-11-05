"""Single host entry point. No credentials, background jobs or live auto-start."""
import os
from contextlib import closing
import sqlite3
from pathlib import Path
from .agents import READ_ROLES, AgentRouter, transport
from .cases import CaseStore
from .cleanup import Cleanup
from .common import digest, PreDispatchRejected
from .delegation import DelegatingHost
from .evolution import EvolutionStore
from .mint import MintCoordinator
from .orders import OrderStore
from .planning import TemplateStore
from .routing import PriorityRouter
from .workflow import Workflow
from .patterns import PatternStore
from .wallet_queue import WalletQueue
from .services import ServiceControls
from .research_cache import ResearchCache
from .state_ops import migrate, snapshot
from .claims import ClaimLedger


class BotRuntime:
    def __init__(self, state_directory, owner, config, host, *, send=None):
        if not isinstance(owner,str) or not owner:
            raise ValueError('trusted installation owner required')
        cleanup_config=config.get('approval_cleanup',{})
        if cleanup_config.get('enabled',True) is not True or cleanup_config.get('exemptions',[]):
            raise ValueError('cleanup cannot be disabled or exempt permissions')
        limits=config.get('limits',{})
        custom=any(limits.get(k) is not None for k in ['per_transaction_amount_wei','daily_amount_wei','per_transaction_shares']) or limits.get('mode','unlimited')!='unlimited' or config.get('referrer_address') is not None
        if custom:
            validator=getattr(host,'validate_personal_config',None)
            if not callable(validator) or validator(config) is not True:
                raise PermissionError('custom limits/referrer require an implemented and validated host policy')
        root=Path(state_directory).expanduser().resolve()
        source=Path(__file__).resolve().parents[1]
        if root==source or source in root.parents:
            raise ValueError('private state must be outside distributable source')
        root.mkdir(parents=True,exist_ok=True,mode=0o700)
        path=root/'bot.sqlite'
        if path.is_symlink():raise ValueError('private database cannot be a symlink')
        try:
            fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        except FileExistsError:pass
        # Reject a foreign owner before migrations or backup creation can mutate
        # that installation. Retain the transactional owner check below as well.
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='installation'").fetchone():
                previous=db.execute('SELECT owner FROM installation WHERE id=1').fetchone()
                if previous and previous[0]!=owner:
                    raise PermissionError('database belongs to a different installation owner')
        migrate(path)
        self.path=str(path);self.config=config
        # One installation DB cannot silently become another user's case store.
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS installation (id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO installation VALUES (1,?)',(owner,))
            if db.execute('SELECT owner FROM installation WHERE id=1').fetchone()[0]!=owner:
                raise PermissionError('database belongs to a different installation owner')
        self.owner,self.host=owner,host
        self._resources=[]
        def keep(resource):self._resources.append(resource);return resource
        try:
            agents=config.get('agents',{})
            self.services=keep(ServiceControls(str(path),config.get('research_budget')))
            self.cache=keep(ResearchCache(str(path)))
            def metered(provider,envelope):
                role='task.'+envelope['operation'] if 'operation' in envelope else envelope['role']
                import uuid
                key=envelope['task_id'] if 'operation' not in envelope else str(uuid.uuid4())
                quote=host.research_cost_ceiling(provider,role) if hasattr(host,'research_cost_ceiling') else None
                try:self.services.reserve(key,digest(provider),role,quote)
                except (ValueError,PermissionError) as exc:raise PreDispatchRejected(str(exc)) from exc
                return (send or transport)(provider,envelope)
            def native(envelope):
                role='task.'+envelope['operation'] if 'operation' in envelope else envelope['role']
                import uuid
                key=envelope['task_id'] if 'operation' not in envelope else str(uuid.uuid4())
                quote=host.research_cost_ceiling({'transport':'native'},role) if hasattr(host,'research_cost_ceiling') else None
                try:self.services.reserve(key,'native',role,quote)
                except (ValueError,PermissionError) as exc:raise PreDispatchRejected(str(exc)) from exc
                return host.run_original_agent(envelope)
            def available(name,role):
                accepted=self.services.available(name,agents['providers'][name],role)
                health=getattr(host,'cached_agent_availability',None)
                return accepted and (health is None or health(name,role) is True)
            self._metered=metered
            self.router=keep(PriorityRouter(str(path),agents.get('providers',{}),agents.get('routes',{}),
                native,host.verified_original_methods,send=metered,
                disclose=getattr(host,'accept_external_cost_and_data',None),available=available,
                fallback_policy=agents.get('fallback_policy','automatic'),wait_seconds=agents.get('fallback_wait_seconds',60),
                native_control=getattr(host,'original_task_control',())))
            self.claims=keep(ClaimLedger(str(path)))
            self.cases=keep(CaseStore(str(path)));self.cleanup=keep(Cleanup(str(path)))
            self.evolution=keep(EvolutionStore(str(path)))
            self.orders=keep(OrderStore(str(path)));self.templates=keep(TemplateStore(str(path)))
            self.patterns=keep(PatternStore(str(path)));self.wallet_queue=keep(WalletQueue(str(path)))
            self.workflow=Workflow(self.router,self.cases,self.cleanup,self.evolution,self.patterns)
            self.coordinator=MintCoordinator(self.orders,self.templates,self.workflow,self.wallet_queue)
            self.delegated_host=DelegatingHost(host,self.router,self.wallet_queue,self.claims,self.owner)
        except Exception:
            self.close();raise
    def close(self):
        for resource in reversed(self._resources):resource.close()
        self._resources=[]
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
    def _task(self,request_id):
        if not isinstance(request_id,str) or not request_id:raise ValueError('stable incoming request id required')
        return 'user:'+digest(dict(owner=self.owner,request_id=request_id))
    def read(self,request_id,role,payload):
        if role not in READ_ROLES:raise PermissionError('use confirmed order entry point for writes')
        role={'project.describe':'project.brief','participation.explain':'project.brief'}.get(role,role)
        reviewer=getattr(self.host,'review_research_result',None)
        ttl=self.config.get('research_cache',{}).get('ttl_seconds',300) if reviewer else 0
        return self.cache.call(self._task(request_id),role,payload,digest(dict(agents=self.config.get('agents',{}),methods=self.host.verified_original_methods)),
            ttl,self.router.call,reviewer or (lambda *args:False))
    def discover(self,request_id,evidence):return self.read(request_id,'discovery',{'evidence':evidence})
    def brief(self,request_id,evidence):return self.read(request_id,'project.brief',{'evidence':evidence})
    def describe(self,request_id,evidence):return self.brief(request_id,evidence)
    def explain(self,request_id,evidence):return self.brief(request_id,evidence)
    @staticmethod
    def project_view(result,section):
        if section not in {'summary','participation','risks','sources'}:raise ValueError('unknown brief section')
        if result.get('status')!='completed':return result
        return dict(status='completed',section=section,content=result['data'][section])
    def select(self,request_id,intent,*,explicit_repeat=False):
        self._task(request_id)
        accounts=self.config.get('wallet',{}).get('accounts',[])
        if accounts:
            from .common import address
            if address(intent['wallet']) not in {address(a) for a in accounts}:
                raise PermissionError('wallet not in configured installation accounts')
        return self.orders.create(self.owner,request_id,intent,explicit_repeat=explicit_repeat)
    def prepare(self,order_id,snapshot,resolution,evidence,features=None):
        return self.coordinator.prepare(self.owner,order_id,snapshot,resolution,evidence,self.delegated_host,features)
    def confirm(self,order_id):
        order=self.orders.get(self.owner,order_id)
        with self.wallet_queue.guard(order) as held:
            if held:self.wallet_queue.recover_pre_dispatch(self.orders.get(self.owner,order_id))
        # User interaction must not hold a wallet execution lock.
        order=self.orders.confirm(self.owner,order_id,self.host)
        self.wallet_queue.enqueue(order,order['plan_review']['cost_totals'])
        return order
    def _track_entitlements(self,result):
        if 'id' in result:self.claims.scan_order(self.owner,result,self.host)
        return result
    def execute(self,order_id):return self._track_entitlements(self.coordinator.execute(self.owner,order_id,self.delegated_host))
    def reconcile(self,order_id):return self._track_entitlements(self.coordinator.reconcile(self.owner,order_id,self.delegated_host))
    def track_entitlement(self,value):
        review=self.host.review_entitlement(value)
        return self.claims.register(self.owner,value,review)
    def claims_readiness(self):
        groups={
            'automatic_discovery':['entitlements_for_order'],
            'manual_registration':['review_entitlement'],
            'direct_refresh':['inspect_entitlement'],
            'agent_evidence_review':['verify_entitlement_evidence'],
            'external_claim_recording':['verify_claim_event'],
            'terminal_correction':['verify_claim_event','review_claim_correction']}
        return dict(capabilities={name:all(callable(getattr(self.host,m,None)) for m in methods) for name,methods in groups.items()},
            missing_callbacks=sorted({m for methods in groups.values() for m in methods if not callable(getattr(self.host,m,None))}),
            verified_live=False,scheduler_started=False)
    def pending_claims(self):return self.claims.dashboard(self.owner)
    def claim_history(self,entitlement_id=None):return self.claims.history(self.owner,entitlement_id)
    def claim_events(self,attempt_id=None):return self.claims.events(self.owner,attempt_id)
    def review_claim(self,entitlement_id,attempt_id,evidence):
        entry=self.claims.get(self.owner,entitlement_id)
        current=next((x for x in self.claim_history(entitlement_id) if x['attempt_id']==attempt_id),None)
        if current is None:raise ValueError('unknown claim attempt')
        fact=self.host.verify_claim_event(entry,evidence)
        review=self.host.review_claim_correction(entry,current,fact)
        return self.claims.review_claim(self.owner,entitlement_id,attempt_id,fact,review)
    def review_case_outcomes(self,case_id,evidence):
        review=self.host.review_case_outcomes(case_id,evidence)
        return self.cases.review_outcomes(case_id,review)
    def record_claim(self,entitlement_id,attempt_id,evidence):
        entitlement=self.claims.get(self.owner,entitlement_id)
        fact=self.host.verify_claim_event(entitlement,evidence)
        return self.claims.record_claim(self.owner,entitlement_id,attempt_id,fact)
    def refresh_claims(self):
        # Catch up older finalized mint orders after upgrade; all calls are reads.
        import json
        rows=self.orders.db.execute('SELECT record FROM mint_orders WHERE owner=?',(self.owner,)).fetchall()
        for raw, in rows:self.claims.scan_order(self.owner,json.loads(raw),self.host)
        errors=[]
        for entry in self.pending_claims()['entitlements']:
            if not entry['check_due']:continue
            try:
                fact=self.delegated_host.inspect_entitlement(entry)
                for event in fact.get('claim_events',[]):
                    if not isinstance(event,dict) or not isinstance(event.get('attempt_id'),str) or not event['attempt_id']:
                        raise ValueError('claim event requires stable attempt id')
                    self.claims.record_claim(self.owner,entry['id'],event['attempt_id'],event)
                self.claims.observe(self.owner,entry['id'],fact)
            except Exception as exc:
                errors.append(dict(entitlement_id=entry['id'],error=type(exc).__name__))
        return dict(**self.pending_claims(),check_errors=errors)

    def recover_cleanup(self,order_id):
        order=self.orders.get(self.owner,order_id)
        with self.wallet_queue.guard(order) as held:
            if not held:raise RuntimeError('wallet worker active')
            order=self.orders.get(self.owner,order_id)
            if order['state'] not in {'failed','mint_verified'} or order.get('result',{}).get('finalized') is not True:
                raise ValueError('permission recovery requires a finalized order')
            if self.cleanup.db.execute('SELECT 1 FROM workflows WHERE id=?',(order_id,)).fetchone():
                raise ValueError('permission journal already exists')
            fact=self.host.verify_used_approvals(order)
            from .common import address
            if (fact.get('verified') is not True or not fact.get('evidence_ref') or fact.get('sequence_terminal') is not True
                or fact.get('order_id')!=order_id or fact.get('chain_id')!=order['intent']['chain_id']
                or address(fact.get('wallet'))!=order['intent']['wallet'] or not isinstance(fact.get('approvals'),list)):
                raise PermissionError('independent complete used-permission recovery required')
            self.cleanup.track(order_id,dict(chain_id=order['intent']['chain_id'],wallet=order['intent']['wallet']),fact['approvals'])
        return self.reconcile(order_id)

    def cancel(self,order_id):
        result=self.orders.cancel(self.owner,order_id)
        self.wallet_queue.release(order_id,no_submission=True)
        return result
    def status(self,order_id):
        card=self.orders.card(self.owner,order_id)
        card['wallet_queue']=next((x for x in self.wallet_queue.status() if x['order_id']==order_id),None)
        return card

    def probe_provider(self,name,role,payload,request_id,expires_at):
        provider=self.config['agents']['providers'][name]
        def invoke(r,p):
            router=AgentRouter(self.path,{name:provider},{r:name},self._metered,self.host.accept_external_cost_and_data)
            try:return router.call('probe:'+self._task(request_id),r,p)
            finally:router.close()
        return self.services.probe(name,provider,role,payload,invoke,self.host.review_provider_probe,expires_at)
    def accept_provider(self,name,role,evidence,expires_at):
        provider=self.config['agents']['providers'][name]
        review=self.host.review_provider_probe(role,{'provider':name},evidence)
        return self.services.accept(name,provider,role,evidence,review,expires_at)
    def task_status(self,request_id,operation='status',provider=None):
        return self.router.control(self._task(request_id),operation,provider)
    def shared_task_status(self,task_id,operation='status',provider=None):
        result=self.router.control(task_id,operation,provider)
        reviewer=getattr(self.host,'review_research_result',lambda *args:False)
        return self.cache.complete(task_id,result,reviewer)
    def export_state(self,path):return snapshot(self.path,path)
    def select_many(self,request_id,intents):
        # Explicit per-wallet intents only; never copy one wallet's proof or balance.
        return [self.select(request_id+':'+str(i),intent) for i,intent in enumerate(intents)]

    def withdraw_contract(self,chain,target,reason):
        import json
        from .common import address
        review=self.host.review_contract_withdrawal(chain,target,reason)
        self.cases.set_contract_block(chain,target,True,reason,review)
        ids=[]
        for key,raw in self.cases.db.execute('SELECT id,record FROM cases'):
            record=json.loads(raw)['snapshot']
            if record['chain_id']==chain and address(record['address'])==address(target):ids.append(key)
        invalidated=self.orders.invalidate_audits(self.owner,ids)
        for order_id in invalidated:self.wallet_queue.release(order_id,no_submission=True)
        self.cache.invalidate()
        return dict(invalidated_orders=invalidated,patterns_changed=False)

    def readiness(self,capabilities):
        from .onboarding import setup_status
        accepted={name:[role for role in provider.get('capabilities',[]) if self.services.available(name,provider,role)]
                  for name,provider in self.config.get('agents',{}).get('providers',{}).items()}
        return setup_status(self.config,{**capabilities,'accepted_provider_roles':accepted})

    def analysis_status(self,task_id,operation='status'):
        if not task_id.startswith('analysis:'):raise ValueError('expected delegated analysis task id')
        return self.router.control(task_id,operation)
