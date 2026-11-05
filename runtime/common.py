import hashlib
import json
import re


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def address(value):
    if not isinstance(value, str) or not re.fullmatch(r'0x[0-9a-fA-F]{40}', value):
        raise ValueError('invalid EVM address')
    return value.lower()


def uint(value):
    if not isinstance(value, str) or not re.fullmatch(r'0|[1-9][0-9]*', value):
        raise ValueError('amount must be a decimal integer string')
    return value


def transaction(raw):
    if type(raw.get('chain_id')) is not int or raw['chain_id'] <= 0:
        raise ValueError('invalid chain')
    data = raw.get('data')
    if not isinstance(data, str) or not re.fullmatch(r'0x(?:[a-fA-F0-9]{2})*', data):
        raise ValueError('invalid calldata')
    return dict(chain_id=raw['chain_id'], wallet=address(raw['wallet']),
                to=address(raw['to']), value_wei=uint(raw['value_wei']), data=data.lower())


class PreDispatchRejected(PermissionError):
    """Trusted local guard refused before transport; no remote side effect."""


class NeedsAgent(Exception):
    """Host has a specific semantic gap, not a network/permission failure."""
    def __init__(self, reason, evidence_version, missing=()):
        if not reason or not evidence_version:
            raise ValueError('explicit question and observed evidence version required')
        self.question=dict(reason=reason,evidence_version=evidence_version,missing=list(missing))
        super().__init__(reason)


class AgentPending(RuntimeError):
    def __init__(self, task_id, role):
        self.task_id,self.role=task_id,role
        super().__init__('analysis pending: '+task_id)
