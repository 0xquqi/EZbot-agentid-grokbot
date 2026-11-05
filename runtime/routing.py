"""External-first dispatch with durable selection and native Skill fallback."""
import json
import sqlite3
import time
from urllib.error import URLError
from .agents import AgentRouter, READ_ROLES, WRITE_ROLES, METHOD_ROLES
from .research_method import descriptor
from .common import digest

NATIVE = '__native__'


class PriorityRouter:
    def __init__(self, path, providers, routes, native_send, native_methods,
                 send=None, disclose=None, available=None, fallback_policy="automatic", wait_seconds=60, native_control=()):
        if fallback_policy not in {'automatic','wait','notify'}:raise ValueError('invalid read fallback policy')
        if type(wait_seconds) is not int or wait_seconds<0:raise ValueError('invalid fallback delay')
        self.fallback_policy,self.wait_seconds=fallback_policy,wait_seconds
        self.path=path
        if any(p.get('transport')=='native' for p in providers.values()):
            raise ValueError('native transport is reserved for the original host')
        if NATIVE in providers:
            raise ValueError('reserved provider name')
        self.db=sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS route_selection (id TEXT PRIMARY KEY, binding TEXT, provider TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS route_wait (id TEXT PRIMARY KEY, until REAL)')
        self.providers=providers;self.routes=routes;self.available=available
        native=dict(capabilities=sorted(READ_ROLES|WRITE_ROLES),installed_methods=native_methods,transport='native',task_control=list(native_control))
        from .agents import transport
        remote_send=send or transport
        def dispatch(config,envelope):
            return native_send(envelope) if config.get('transport')=='native' else remote_send(config,envelope)
        def consent(details):
            return True if details['provider']==NATIVE else disclose is not None and disclose(details) is True
        self.router=AgentRouter(path,{**providers,NATIVE:native},{},dispatch,consent)

    def close(self):
        self.router.close();self.db.close()

    def _choose(self,role):
        name=self.routes.get(role,self.routes.get('*'))
        if name is None:return NATIVE
        if name not in self.providers:raise ValueError('configured provider not found')
        config=self.providers[name]
        if role not in config.get('capabilities',[]):return NATIVE
        if role in METHOD_ROLES:
            method=descriptor()
            if config.get('installed_methods',{}).get(method['name'])!=method['method_digest']:return NATIVE
        if self.available is not None and self.available(name,role) is not True:return NATIVE
        return name

    def call(self,task_id,role,payload,authorize=None):
        if role not in READ_ROLES|WRITE_ROLES:raise ValueError('unsupported role')
        binding=digest(dict(role=role,payload=payload))
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            old=self.db.execute('SELECT binding,provider FROM route_selection WHERE id=?',(task_id,)).fetchone()
            if old:
                if old[0]!=binding:raise ValueError('task changed after provider selection')
                selected=old[1]
            else:
                selected=self._choose(role)
                self.db.execute('INSERT INTO route_selection VALUES (?,?,?)',(task_id,binding,selected))
        waiting=self.db.execute('SELECT until FROM route_wait WHERE id=?',(task_id,)).fetchone()
        if waiting:
            if waiting[0] is None or waiting[0]>time.time():
                return dict(status='pending',data=dict(task_id=task_id,reason='fallback_waiting',retry_after=waiting[0]))
            self.allow_fallback(task_id)
            selected=NATIVE
        # Per-call router objects prevent cross-role mutation during dispatch.
        route=AgentRouter(self.path,
                          self.router.providers,{role:selected},self.router.send,self.router.disclose)
        try:
            try:
                return route.call('routed:'+digest(dict(task_id=task_id,provider=selected)),role,payload,authorize)
            except (TimeoutError,ConnectionError,URLError):
                # Read-only computation can fall back. Consent refusal, invalid
                # evidence, pending jobs, and ANY writes never auto-switch.
                if selected==NATIVE or role in WRITE_ROLES:raise
                if self.fallback_policy!='automatic':
                    until=time.time()+self.wait_seconds if self.fallback_policy=='wait' else None
                    with self.db:self.db.execute('INSERT OR REPLACE INTO route_wait VALUES (?,?)',(task_id,until))
                    return dict(status='pending',data=dict(task_id=task_id,reason='fallback_waiting',retry_after=until))
                with self.db:
                    self.db.execute('UPDATE route_selection SET provider=? WHERE id=?',(NATIVE,task_id))
                native=AgentRouter(self.path,
                    self.router.providers,{role:NATIVE},self.router.send,self.router.disclose)
                try:
                    return native.call('routed:'+digest(dict(task_id=task_id,provider=NATIVE)),role,payload,authorize)
                finally:native.close()
        finally:route.close()

    def allow_fallback(self,task_id):
        if not self.db.execute('SELECT 1 FROM route_wait WHERE id=?',(task_id,)).fetchone():
            raise ValueError('no read fallback decision pending')
        # Only read connection failures can create route_wait records.
        with self.db:
            self.db.execute('UPDATE route_selection SET provider=? WHERE id=?',(NATIVE,task_id))
            self.db.execute('DELETE FROM route_wait WHERE id=?',(task_id,))

    def control(self,task_id,operation='status',provider=None):
        row=self.db.execute('SELECT provider FROM route_selection WHERE id=?',(task_id,)).fetchone()
        if not row:raise ValueError('unknown logical task')
        selected=provider if provider is not None else row[0]
        routed_id='routed:'+digest(dict(task_id=task_id,provider=selected))
        # Metadata lookup proves this logical request actually dispatched there;
        # this also allows querying/cancelling the original read after fallback.
        return self.router.control(routed_id,operation)
