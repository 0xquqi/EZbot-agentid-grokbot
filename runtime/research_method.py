"""Pin the user's original research skill; no private case corpus is bundled."""
import hashlib
import json
from pathlib import Path
from .common import digest, address


def descriptor():
    lock=json.loads((Path(__file__).resolve().parents[1]/'config/research-method.lock.json').read_text())
    return dict(name=lock['name'], repository=lock['repository'], commit=lock['commit'],
                skill_path=lock['skill_path'], entrypoint=lock['entrypoint'],
                method_digest=digest(lock), scope='agent_id_mint',
                requirement='Use the original skill and referenced resources. Reuse supplied host-verified current evidence and target-role resolution; collect only missing or stale facts. Never guess the target from the first address.')


def verify_installation(skill_directory):
    """Trusted host checks the complete installation once; no scripts executed."""
    root=Path(skill_directory).resolve()
    lock=json.loads((Path(__file__).resolve().parents[1]/'config/research-method.lock.json').read_text())
    for name,expected in lock['files'].items():
        path=root/name
        if not path.is_file() or path.is_symlink() or root not in path.resolve().parents:
            raise ValueError('missing/invalid research method resource: '+name)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            raise ValueError('research method version mismatch: '+name)
    return descriptor()['method_digest']


def require_target(snapshot, resolution):
    # resolution is supplied by trusted host after checking the original research
    # output. A remote agent's self-reported "verified" is not sufficient.
    if (not isinstance(resolution,dict) or resolution.get('host_verified') is not True
            or resolution.get('role')!='mint' or not resolution.get('sources')
            or not resolution.get('review_ref')):
        raise ValueError('verified mint target and role evidence required before case matching')
    if resolution.get('chain_id')!=snapshot['chain_id'] or address(resolution['address'])!=address(snapshot['address']):
        raise ValueError('resolved mint target differs from audit snapshot')
