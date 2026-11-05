"""Vendor-neutral JSON interface. Installers supply endpoints or local adapters."""
import json
import math
import os
import sqlite3
import subprocess
import selectors
import signal
import tempfile
import time
import urllib.request
from urllib.parse import urlparse
from .common import digest, transaction, PreDispatchRejected
from .research_method import descriptor

READ_ROLES = {'project.brief', 'discovery', 'project.describe', 'participation.explain', 'research',
              'contract.resolve', 'audit', 'audit.prepare', 'trade.prepare',
              'receipt.inspect', 'approval.inspect', 'followup.plan', 'case.summarize'}
METHOD_ROLES = {'project.brief','discovery','project.describe','participation.explain','research',
                'contract.resolve','audit','audit.prepare'}
WRITE_ROLES = {'trade.execute', 'approval.revoke'}
MAX_BYTES = 2_000_000


def _stdio(argv, body, env, timeout):
    """Bound output while reading; a slow or oversized adapter cannot fill RAM."""
    deadline=time.monotonic()+timeout
    with tempfile.TemporaryFile() as input_file:
        input_file.write(body);input_file.seek(0)
        with subprocess.Popen(argv,stdin=input_file,stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,env=env,start_new_session=True) as process:
            try:
                raw=bytearray()
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout,selectors.EVENT_READ)
                    while True:
                        remaining=deadline-time.monotonic()
                        if remaining<=0 or not selector.select(remaining):
                            raise TimeoutError('adapter timed out')
                        chunk=os.read(process.stdout.fileno(),min(65536,MAX_BYTES+1-len(raw)))
                        if not chunk:break
                        raw.extend(chunk)
                        if len(raw)>MAX_BYTES:raise ValueError('response too large')
                try:code=process.wait(timeout=max(0,deadline-time.monotonic()))
                except subprocess.TimeoutExpired as exc:raise TimeoutError('adapter timed out') from exc
                if code:raise subprocess.CalledProcessError(code,argv)
                return bytes(raw)
            finally:
                # Also close adapter descendants holding the output pipe open.
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                process.wait()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('endpoint redirect forbidden')


def transport(config, envelope):
    """Config is installer-controlled, never derived from a website/agent answer."""
    body = json.dumps(envelope).encode()
    timeout = config.get('timeout_seconds', 60)
    if config['transport'] == 'https':
        u = urlparse(config['url'])
        if u.scheme != 'https' or not u.hostname or u.username or u.password or u.fragment:
            raise ValueError('HTTPS endpoint without embedded credentials required')
        headers = {'Content-Type': 'application/json'}
        if config.get('auth_env'):
            headers['Authorization'] = 'Bearer ' + os.environ[config['auth_env']]
        req = urllib.request.Request(config['url'], body, headers, method='POST')
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=timeout) as r:
            raw = r.read(MAX_BYTES + 1)
    elif config['transport'] == 'stdio':
        argv = config['argv']
        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
            raise ValueError('explicit argv required')
        if not os.path.isabs(argv[0]):
            raise ValueError('adapter executable must be absolute')
        env = {k: os.environ[k] for k in ['PATH', 'LANG'] + config.get('pass_env', []) if k in os.environ}
        # No shell; no automatic inheritance of host wallet sessions or API keys.
        raw = _stdio(argv,body,env,timeout)
    else:
        raise ValueError('unsupported transport; wrap other protocols in a local adapter')
    if len(raw) > MAX_BYTES:
        raise ValueError('response too large')
    return json.loads(raw)


class AgentRouter:
    def __init__(self, path, providers, routes, send=transport, disclose=None):
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS dispatch (id TEXT PRIMARY KEY, digest TEXT, state TEXT, result TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS dispatch_metadata (id TEXT PRIMARY KEY, provider TEXT, config TEXT, envelope TEXT)')
        self.providers, self.routes, self.send = providers, routes, send
        self.disclose = disclose

    def close(self):
        self.db.close()

    def call(self, task_id, role, payload, authorize=None):
        if role not in READ_ROLES | WRITE_ROLES:
            raise ValueError('unsupported role')
        provider = self.providers[self.routes[role]]
        if role not in provider.get('capabilities', []):
            raise ValueError('provider lacks capability')
        if role in METHOD_ROLES:
            method=descriptor()
            if provider.get('installed_methods',{}).get(method['name'])!=method['method_digest']:
                raise ValueError('original EZbot research skill is not verified on this provider')
            payload={**payload,'required_method':method}
        if role in WRITE_ROLES:
            if role == 'trade.execute':
                transaction(payload['transaction'])
                if not payload.get('preview_request_id') or not payload.get('audit_ref'):
                    raise ValueError('preview and reviewed audit required')
            elif not payload.get('approval'):
                raise ValueError('specific approval required')
        body_hash = digest(dict(role=role, payload=payload, provider=provider))
        prior = self.db.execute('SELECT digest,state,result FROM dispatch WHERE id=?', (task_id,)).fetchone()
        if prior:
            if prior[0] != body_hash:
                raise ValueError('task id reused with different parameters')
            if prior[1] == 'returned':
                return json.loads(prior[2])
            if prior[1]!='pre_dispatch_failed':
                raise RuntimeError('outcome unknown; reconcile task id with provider before any resubmission')
        # This trusted callback must check wallet Skill disclosures and actual UI approval.
        # No "approved: true" field supplied by an external agent grants permission.
        if role in WRITE_ROLES and (authorize is None or authorize(role, payload, body_hash) is not True):
            raise PreDispatchRejected('wallet confirmation required')
        if self.disclose is None or self.disclose(dict(provider=self.routes[role], role=role,
                billing=provider.get('billing', 'unknown'), payload=payload, request_digest=body_hash)) is not True:
            raise PreDispatchRejected('external cost and data disclosure acceptance required')
        envelope = dict(protocol='agentid-task/v1', task_id=task_id, role=role,
                        payload=payload, request_digest=body_hash)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if prior:
                changed=self.db.execute("UPDATE dispatch SET state='dispatching',result=NULL WHERE id=? AND state='pre_dispatch_failed'",(task_id,)).rowcount
                if changed!=1:raise RuntimeError('task changed concurrently; reconcile before resubmission')
            else:
                self.db.execute('INSERT INTO dispatch VALUES (?, ?, ?, NULL)', (task_id, body_hash, 'dispatching'))
                self.db.execute('INSERT INTO dispatch_metadata VALUES (?,?,?,?)',(task_id,self.routes[role],digest(provider),json.dumps(envelope)))
        # Persist before dispatch. Timeout/crash never causes an automatic write retry.
        try:result = self.send(provider, envelope)
        except PreDispatchRejected as exc:
            result=dict(task_id=task_id,request_digest=body_hash,status='failed',data={'reason':'local_pre_dispatch_rejection','detail':str(exc)})
            with self.db:self.db.execute('UPDATE dispatch SET state=?,result=? WHERE id=?',('pre_dispatch_failed',json.dumps(result),task_id))
            raise
        self._validate(result,envelope)
        return self._store_result(task_id,result)  # NOT independently verified chain state.

    def _store_result(self,task_id,result):
        # Status requests can finish out of order, including during initial send.
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.db.execute('SELECT result FROM dispatch WHERE id=?',(task_id,)).fetchone()
            if row and row[0]:
                previous=json.loads(row[0])
                if previous['status'] in {'completed','failed','cancelled'}:return previous
            self.db.execute('UPDATE dispatch SET state=?,result=? WHERE id=?',('returned',json.dumps(result),task_id))
        return result

    def _validate(self,result,envelope):
        if result.get('task_id')!=envelope['task_id'] or result.get('request_digest')!=envelope['request_digest']:
            raise ValueError('response binding mismatch')
        if result.get('status') not in {'completed','pending','needs_confirmation','failed','cancelled'} or not isinstance(result.get('data'),dict):
            raise ValueError('invalid structured response')
        if envelope['role']=='project.brief' and result['status']=='completed':
            data=result['data']
            if len(json.dumps(data).encode())>100000:
                raise ValueError('project brief too large')
            if any(isinstance(data.get(k),list) and len(data[k])>100 for k in ['participation','risks','sources']):
                raise ValueError('too many project brief items')
            if (not isinstance(data.get('summary'),str) or not data['summary']
                or not isinstance(data.get('participation'),list) or not isinstance(data.get('risks'),list)
                or not isinstance(data.get('sources'),list) or not data['sources']
                or type(data.get('observed_at')) not in {int,float}
                or not math.isfinite(data['observed_at']) or data['observed_at']<=0):
                raise ValueError('combined project brief requires summary, participation, risks, sources and observation time')
        if envelope['role'] in METHOD_ROLES and result['status']=='completed':
            if result['data'].get('method_digest')!=envelope['payload']['required_method']['method_digest']:
                raise ValueError('provider did not acknowledge required method')

    def control(self,task_id,operation):
        if operation not in {'status','cancel'}:raise ValueError('unsupported task control')
        row=self.db.execute('SELECT provider,config,envelope FROM dispatch_metadata WHERE id=?',(task_id,)).fetchone()
        if not row:raise ValueError('no original dispatched task to reconcile')
        name,config_hash,raw=row;envelope=json.loads(raw);provider=self.providers[name]
        if digest(provider)!=config_hash:raise ValueError('original provider configuration changed; no blind rerouting')
        if operation=='cancel' and envelope['role'] in WRITE_ROLES:
            raise PermissionError('wallet transaction cancellation needs separate wallet authorization; status only')
        state,previous=self.db.execute('SELECT state,result FROM dispatch WHERE id=?',(task_id,)).fetchone()
        if state=='pre_dispatch_failed':return json.loads(previous)
        if previous and json.loads(previous)['status'] in {'completed','failed','cancelled'}:return json.loads(previous)
        if operation not in provider.get('task_control',[]):raise ValueError('provider lacks task control capability')
        control=dict(protocol='agentid-task-control/v1',operation=operation,task_id=task_id,
                     request_digest=envelope['request_digest'],role=envelope['role'])
        if self.disclose is None or self.disclose(dict(provider=name,role='task.'+operation,
            billing=provider.get('billing','unknown'),payload=control,request_digest=digest(control))) is not True:
            raise PermissionError('task control cost/data acceptance required')
        result=self.send(provider,control)
        self._validate(result,envelope)
        return self._store_result(task_id,result)  # Not a wallet receipt.
