"""Single-flight, reviewed, expiring descriptive research. Never cache live state."""
import json
import sqlite3
import time
from .common import digest, PreDispatchRejected

CACHE_ROLES={'project.brief','project.describe','participation.explain'}


class ResearchCache:
    def __init__(self,path,clock=time.time):
        self.db=sqlite3.connect(path);self.clock=clock
        self.db.execute('CREATE TABLE IF NOT EXISTS research_reviews(task TEXT PRIMARY KEY, record TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS research_request_keys(id TEXT PRIMARY KEY, binding TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS research_cache(key TEXT PRIMARY KEY, task TEXT, role TEXT, payload TEXT, state TEXT, expires REAL, result TEXT)')
    def close(self):self.db.close()
    def call(self,task,role,payload,version,ttl,dispatch,review):
        if role not in CACHE_ROLES or ttl<=0:return dispatch(task,role,payload)
        key=digest(dict(role=role,payload=payload,version=version));now=self.clock()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            binding=self.db.execute('SELECT binding FROM research_request_keys WHERE id=?',(task,)).fetchone()
            if binding and binding[0]!=key:raise ValueError('same research request changed parameters or method version')
            self.db.execute('INSERT OR IGNORE INTO research_request_keys VALUES (?,?)',(task,key))
            old=self.db.execute('SELECT task,state,expires,result FROM research_cache WHERE key=?',(key,)).fetchone()
            if old and old[1] in {'complete','rejected'} and old[2]>now:
                cached=json.loads(old[3])
                # Old versions stored rejected raw output as completed.
                if old[1]=='rejected' and cached.get('status')=='completed':
                    return dict(status='failed',data={'reason':'research_review_rejected'})
                return cached
            if old and old[1]=='pending':
                expired=old[2]<=now
                return dict(status='pending',data=dict(shared_task_id=old[0],reason='reconciliation_required' if expired else 'research_in_progress',expired=expired,recovery_hint='call shared_task_status with shared_task_id; do not resubmit unknown work'))
            # Expired entries get a new immutable dispatch id, even if incoming id is reused.
            import uuid
            dispatch_id='cache:'+str(uuid.uuid4())
            self.db.execute('INSERT OR REPLACE INTO research_cache VALUES (?,?,?,?,?,?,NULL)',(key,dispatch_id,role,json.dumps(payload),'pending',now+ttl))
        try:result=dispatch(dispatch_id,role,payload)
        except PreDispatchRejected:
            with self.db:self.db.execute("UPDATE research_cache SET state='rejected',expires=0,result=? WHERE task=?",(json.dumps(dict(status='failed',data={'reason':'local_pre_dispatch_rejection'})),dispatch_id))
            raise
        return self.complete(dispatch_id,result,review)
    def complete(self,task,result,review):
        row=self.db.execute('SELECT role,payload,expires FROM research_cache WHERE task=?',(task,)).fetchone()
        if not row:raise ValueError('unknown shared research task')
        if result.get('status') in {'pending','needs_confirmation'}:return {**result,'shared_task_id':task}
        decision=review(row[0],json.loads(row[1]),result) if result.get('status')=='completed' else None
        accepted=decision is True or (isinstance(decision,dict) and decision.get('verified') is True and bool(decision.get('reviewer')) and bool(decision.get('evidence_ref')))
        record=decision if isinstance(decision,dict) else dict(verified=accepted,provenance='legacy_boolean' if decision is True else 'not_verified')
        with self.db:self.db.execute('INSERT OR REPLACE INTO research_reviews VALUES (?,?)',(task,json.dumps(dict(review=record,reviewed_at=self.clock()))))
        if result.get('status')=='completed' and not accepted:
            result={**result,'status':'failed','data':{'reason':'research_review_rejected'}}
        with self.db:self.db.execute('UPDATE research_cache SET state=?,result=? WHERE task=?',
            ('complete' if accepted else 'rejected',json.dumps(result),task))
        return result  # Unreviewed output is never reused as a cache hit.
    def pending(self):
        return [dict(task_id=t,role=r) for t,r in self.db.execute("SELECT task,role FROM research_cache WHERE state='pending'")]
    def invalidate(self):
        # Invalidate finished descriptions, never lose active task identifiers.
        with self.db:self.db.execute("UPDATE research_cache SET expires=0")
