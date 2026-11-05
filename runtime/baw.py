"""Original baw approval commands, with host-owned confirmation and chain verification.

No logins, key handling, warning suppression, or blanket auto-confirm flags.
"""
import json
import subprocess
from .common import address


class BawCleanupAdapter:
    def __init__(self, *, executable, verify_session, confirm, authority_reader,
                 workflow_reader, receipt_reader, in_use, runner=None):
        self.executable = executable
        self.verify_session = verify_session
        self.confirm = confirm
        self.authority_reader = authority_reader
        self.workflow_reader = workflow_reader
        self.receipt_reader = receipt_reader
        self.in_use = in_use
        self.runner = runner or self._run

    def _run(self, args):
        # Uses installer's existing local baw session; never sent to another agent.
        out = subprocess.run([self.executable, *args, '--json'], check=True,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
        result = json.loads(out.stdout)
        if result.get('success') is not True:
            raise RuntimeError('baw command failed; inspect locally without exposing credentials')
        return result['data']

    def _check(self, a):
        # Host must run original Skill preflight, verify selected account/chain,
        # CLI version and policy. A provider-supplied boolean is not sufficient.
        if self.verify_session(a['chain_id'], address(a['wallet'])) is not True:
            raise PermissionError('wallet preflight/account verification required')

    def _args(self, command, a):
        return ['approvals', command, '--binanceChainId', str(a['chain_id']),
                '--tokenContract', a['token'], '--spender', a['spender'], '--type', a['kind']]

    def workflow_status(self, workflow, scope):
        return self.workflow_reader(workflow, scope)

    def has_authority(self, a):
        self._check(a)
        # Read latest chain state. Missing list entry or API pagination != zero.
        return self.authority_reader(a)

    def supports_revoke(self, a):
        return a['kind'] in {'approve', 'permit2'}

    def in_use_elsewhere(self, a, workflow):
        return self.in_use(a, workflow)

    def confirm_revoke(self, a):
        self._check(a)
        detail = self.runner(self._args('detail', a))
        # Host displays full token/spender, original warnings, gas, and obtains
        # confirmation according to the unmodified wallet Skill.
        return self.confirm(a, detail) is True

    def revoke(self, a, idempotency_key):
        self._check(a)
        if not self.supports_revoke(a):
            raise ValueError('unsupported approval requires dedicated verified adapter')
        return self.runner(self._args('revoke', a))

    def revoke_status(self, a, submission):
        return self.receipt_reader(a, submission)
