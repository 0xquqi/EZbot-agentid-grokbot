"""Durable per-chain/per-wallet FIFO. Separate wallets progress independently."""
import json
import sqlite3
import fcntl
from pathlib import Path
from contextlib import contextmanager
from .common import address,digest,uint


class WalletQueue:
    def __init__(self,path):
        self.lock_directory=Path(str(path)+".wallet-locks")
        self.db=sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS wallet_jobs(seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, scope TEXT, state TEXT, record TEXT)')
    def close(self):self.db.close()
    def enqueue(self,order,totals):
        scope=digest(dict(chain_id=order['intent']['chain_id'],wallet=address(order['intent']['wallet'])))
        record=dict(plan_digest=digest(order['plan']),totals=totals)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            old=self.db.execute('SELECT scope,state,record FROM wallet_jobs WHERE id=?',(order['id'],)).fetchone()
            if old:
                if old[0]!=scope:raise ValueError('wallet changed in queued order')
                if old[2]!=json.dumps(record) or old[1]=='released':
                    if old[1]=='active':raise ValueError('cannot replace active reservation')
                    self.db.execute('UPDATE wallet_jobs SET record=?,state=? WHERE id=?',(json.dumps(record),'queued',order['id']))
                return
            self.db.execute('INSERT INTO wallet_jobs(id,scope,state,record) VALUES (?,?,?,?)',(order['id'],scope,'queued',json.dumps(record)))
    def acquire(self,order_id,balances):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.db.execute('SELECT seq,scope,state,record FROM wallet_jobs WHERE id=?',(order_id,)).fetchone()
            if not row:raise ValueError('unknown queued order')
            if row[2]=='active':return False
            if row[2]!='queued':return False
            head=self.db.execute("SELECT id FROM wallet_jobs WHERE scope=? AND state IN ('active','queued') ORDER BY seq LIMIT 1",(row[1],)).fetchone()
            if head[0]!=order_id:return False
            totals=json.loads(row[3])['totals']
            if any(int(uint(balances.get(asset,'invalid')))<amount for asset,amount in totals.items()):
                raise ValueError('fresh balance cannot cover reservation')
            self.db.execute("UPDATE wallet_jobs SET state='active' WHERE id=?",(order_id,))
            return True
    def release(self,order_id,*,no_submission=False,terminal=False,cleanup_clean=False):
        if not no_submission and not (terminal and cleanup_clean):
            raise PermissionError('unknown submission/unfinished cleanup retains wallet reservation')
        with self.db:self.db.execute("UPDATE wallet_jobs SET state='released' WHERE id=?",(order_id,))
    def busy(self,chain,wallet,excluding):
        scope=digest(dict(chain_id=chain,wallet=address(wallet)))
        return self.db.execute("SELECT 1 FROM wallet_jobs WHERE scope=? AND state='active' AND id!=?",(scope,excluding)).fetchone() is not None
    def status(self):
        return [dict(order_id=i,state=s,reservation=json.loads(r)) for i,s,r in self.db.execute("SELECT id,state,record FROM wallet_jobs WHERE state!='released' ORDER BY seq")]

    @contextmanager
    def guard(self,order):
        scope=digest(dict(chain_id=order['intent']['chain_id'],wallet=address(order['intent']['wallet'])))
        self.lock_directory.mkdir(mode=0o700,exist_ok=True)
        with open(self.lock_directory/scope,'a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                yield False;return
            try:yield True
            finally:fcntl.flock(lock,fcntl.LOCK_UN)
    def recover_pre_dispatch(self,order):
        # Caller holds guard. No worker can submit concurrently. 'ready' proves
        # the durable dispatch boundary was never crossed, even after a crash.
        if order['state'] in {'needs_refresh','needs_reaudit','preparing','awaiting_confirmation','cancelled'}:
            self.release(order['id'],no_submission=True)
        if order['state']=='ready':
            with self.db:self.db.execute("UPDATE wallet_jobs SET state='queued' WHERE id=? AND state='active'",(order['id'],))
