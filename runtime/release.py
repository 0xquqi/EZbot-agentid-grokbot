"""Build a reviewed, hash-pinned allowlist only. No recursive source export."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def build(root, manifest, output, denylist=()):
    root=Path(root).resolve();output=Path(output).resolve()
    if output==root or root in output.parents:
        raise ValueError('release output must be outside source tree')
    entries=json.loads(Path(manifest).read_text())['files']
    if 'config/release-files.lock.json' in entries:
        raise ValueError('manifest must not include itself')
    checked=[]
    for name,expected in entries.items():
        rel=Path(name)
        if rel.is_absolute() or '..' in rel.parts:
            raise ValueError('invalid release path')
        path=root/rel
        if any((root/Path(*rel.parts[:i])).is_symlink() for i in range(1,len(rel.parts)+1)):
            raise ValueError('symlink forbidden')
        if path.suffix in {'.sqlite','.db','.jsonl','.zip','.gz'} or any(x in rel.parts for x in ['state','private','cases','__pycache__']):
            raise ValueError('private/runtime data forbidden')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=expected:
            raise ValueError('file changed since release review: '+name)
        lower=raw.lower()
        if any(term and term.lower().encode() in lower for term in denylist):
            raise ValueError('private identifier found in release member: '+name)
        checked.append((name,raw))
    # Validate everything before writing. Never overwrite a previously reviewed release.
    with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for name,raw in checked:z.writestr(name,raw)
        z.writestr('config/release-files.lock.json',json.dumps({'format':'agentid-release/v1','files':entries},indent=2)+'\n')
    return {'files':len(checked)+1,'sha256':hashlib.sha256(output.read_bytes()).hexdigest()}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--manifest',required=True);p.add_argument('--output',required=True)
    p.add_argument('--denylist',required=True)
    a=p.parse_args()
    print(json.dumps(build(Path(__file__).resolve().parents[1],a.manifest,a.output,json.loads(Path(a.denylist).read_text()))))
