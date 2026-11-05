"""Durable approval cleanup driven by a trusted wallet adapter, without exemptions."""
import json
import sqlite3
import fcntl
from .common import address, digest, AgentPending


def approval(raw):
    if type(raw.get('chain_id')) is not int or raw['chain_id'] <= 0:
        raise ValueError('chain required')
    if raw.get('kind') not in {'approve', 'permit2', 'erc721', 'operator', 'unknown'}:
        raise ValueError('explicit approval kind required')
    value = {k: address(raw[k]) for k in ['wallet', 'token', 'spender']}
    value.update(chain_id=raw['chain_id'], kind=raw['kind'])
    if raw['kind'] == 'erc721':
        value['token_id'] = str(raw['token_id'])
    if raw['kind'] == 'permit2':
        value['permit2_contract'] = address(raw['permit2_contract'])
    return value


class Cleanup:
    def __init__(self, path):
        self.lock_path = str(path) + '.cleanup.lock.local'
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS cleanup_completion (workflow TEXT PRIMARY KEY, record TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS cleanup (id TEXT PRIMARY KEY, workflow TEXT, approval TEXT, state TEXT, submission TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS workflows (id TEXT PRIMARY KEY, scope TEXT, terminal INTEGER DEFAULT 0)')

    def close(self):
        self.db.close()

    def track(self, workflow, scope, approvals):
        """Before trade/approval: pass verified used + newly granted permissions.

        scope: chain_id, wallet. Does not sweep unrelated historical approvals.
        """
        scope = dict(chain_id=scope['chain_id'], wallet=address(scope['wallet']))
        rows = [approval(x) for x in approvals]
        if any(a['chain_id'] != scope['chain_id'] or a['wallet'] != scope['wallet'] for a in rows):
            raise ValueError('approval outside workflow wallet/chain')
        old = self.db.execute('SELECT scope,terminal FROM workflows WHERE id=?', (workflow,)).fetchone()
        if old and (json.loads(old[0]) != scope or old[1]):
            raise ValueError('workflow scope changed or already terminal')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO workflows(id,scope) VALUES (?,?)', (workflow, json.dumps(scope)))
            for a in rows:
                key = digest(dict(workflow=workflow, approval=a))
                self.db.execute('INSERT OR IGNORE INTO cleanup VALUES (?,?,?,?,NULL)',
                                (key, workflow, json.dumps(a), 'waiting_trade'))

    def abandon_unsent(self,workflow):
        """Only called after proof that this workflow never crossed transport."""
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.db.execute('SELECT terminal FROM workflows WHERE id=?',(workflow,)).fetchone()
            if row and row[0]:raise ValueError('cannot abandon terminal cleanup')
            if self.db.execute("SELECT 1 FROM cleanup WHERE workflow=? AND state!='waiting_trade'",(workflow,)).fetchone():
                raise ValueError('cannot abandon a processed approval')
            self.db.execute('DELETE FROM cleanup WHERE workflow=?',(workflow,))
            self.db.execute('DELETE FROM workflows WHERE id=?',(workflow,))

    def _completed(self,workflow):
        row=self.db.execute('SELECT record FROM cleanup_completion WHERE workflow=?',(workflow,)).fetchone()
        return json.loads(row[0]) if row else None

    def finish(self, workflow, wallet, terminal_evidence=None):
        """wallet verifies complete trade sequence, including revert/aborted paths."""
        completed=self._completed(workflow)
        if completed:return completed
        row = self.db.execute('SELECT scope FROM workflows WHERE id=?', (workflow,)).fetchone()
        if row is None:
            # Missing journal is not proof that no approval was used.
            return {'state':'cleanup_pending','reason':'permission_journal_missing'}
        scope = json.loads(row[0])
        # Must independently reconcile chain/owner/pending orders, not trust agent JSON.
        fact=terminal_evidence or {}
        terminal=(fact.get('verified') is True and fact.get('finalized') is True
            and fact.get('sequence_terminal') is True and bool(fact.get('evidence_ref'))
            and fact.get('order_id')==workflow and fact.get('chain_id')==scope['chain_id']
            and fact.get('wallet')==scope['wallet']
            and fact.get('state') in {'mint_verified','reverted','aborted_no_pending'})
        if not terminal and wallet.workflow_status(workflow, scope) not in {'confirmed', 'reverted', 'aborted_no_pending'}:
            return {'state': 'waiting_trade'}
        with self.db:
            self.db.execute('UPDATE workflows SET terminal=1 WHERE id=?', (workflow,))
        return self.resume(workflow, wallet)

    def resume(self, workflow, wallet):
        # Serialize cleanup across processes; a second worker must not overwrite
        # the first worker's pending write or send the same revoke concurrently.
        with open(self.lock_path, 'a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {'state': 'cleanup_pending', 'reason': 'another_worker_active'}
            return self._resume(workflow, wallet)

    def _resume(self, workflow, wallet):
        completed=self._completed(workflow)
        if completed:return completed
        row = self.db.execute('SELECT scope,terminal FROM workflows WHERE id=?', (workflow,)).fetchone()
        if row is None or not row[1]:
            return {'state': 'waiting_trade'}
        records = self.db.execute('SELECT id,approval,state,submission FROM cleanup WHERE workflow=?', (workflow,)).fetchall()
        states = []
        for key, raw, state, submission in records:
            a = json.loads(raw)
            previous_state = state
            analysis_task_id = None
            try:
                # Read actual current authority even for a previously cleaned record.
                # None/unknown is never treated as zero.
                remaining = wallet.has_authority(a)
                if remaining is False:
                    state = 'clean'
                elif remaining is not True:
                    state = previous_state if previous_state in {'dispatching', 'unknown', 'submitted', 'failed'} else 'verification_unknown'
                elif state in {'dispatching', 'unknown', 'submitted', 'failed'}:
                    # Do not resend ambiguous/failed writes; provider reconciliation first.
                    if submission:
                        status = wallet.revoke_status(a, json.loads(submission))
                        state = 'failed' if status == 'failed' else 'submitted'
                    else:
                        state = 'unknown'
                elif not wallet.supports_revoke(a):
                    state = 'unsupported'
                elif wallet.in_use_elsewhere(a, workflow) is not False:
                    state = 'blocked_in_use'
                elif wallet.confirm_revoke(a) is not True:
                    state = 'needs_confirmation'
                else:
                    # Original Skill risk display/confirmation lives in wallet adapter.
                    with self.db:
                        changed = self.db.execute('UPDATE cleanup SET state=? WHERE id=? AND state=?',
                                                  ('dispatching', key, previous_state)).rowcount
                    if not changed:
                        states.append(dict(approval=a, state='concurrent_processing'))
                        continue
                    state = 'dispatching'
                    response = wallet.revoke(a, idempotency_key=key)
                    submission = json.dumps(response)
                    state = 'submitted'  # txHash is not confirmation.
            except Exception as exc:
                if isinstance(exc,AgentPending):analysis_task_id=exc.task_id
                if state == 'dispatching' or previous_state in {'dispatching', 'unknown'}:
                    state = 'unknown'
                elif submission:
                    state = 'submitted'
                else:
                    state = 'verification_unknown'
            with self.db:
                self.db.execute('UPDATE cleanup SET state=?,submission=? WHERE id=?', (state, submission, key))
            item=dict(approval=a,state=state)
            if analysis_task_id:item['analysis_task_id']=analysis_task_id
            states.append(item)
        result=dict(state='clean' if all(x['state'] == 'clean' for x in states) else 'cleanup_pending', approvals=states)
        if result['state']=='clean':
            # A closed workflow must not revoke permissions granted by later work.
            with self.db:self.db.execute('INSERT OR IGNORE INTO cleanup_completion VALUES (?,?)',(workflow,json.dumps(result)))
        return result
