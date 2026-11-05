"""Offline importer: archive documents are data, never code or authorization."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tarfile
from .cases import CaseStore
from .evolution import EvolutionStore


def import_archive(archive, destination):
    root = Path(__file__).resolve().parents[1]
    destination = Path(destination).resolve()
    if destination == root or root in destination.parents:
        raise ValueError('private case storage must be outside the release source directory')
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('archive import requires a new or empty private directory')
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.chmod(0o700)
    (destination / '.gitignore').write_text('*\n')
    groups = {}
    archive_hash = hashlib.sha256(Path(archive).read_bytes()).hexdigest()
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            parts = PurePosixPath(member.name).parts
            if 'examples' not in parts or not member.isfile():
                continue
            index = parts.index('examples')
            if len(parts) != index + 3 or '..' in parts or member.size > 2_000_000:
                continue
            name = parts[-1]
            if not (name.endswith('.json') or name == 'NOTES.md'):
                continue
            raw = tar.extractfile(member).read()
            groups.setdefault(parts[index+1], {})[name] = (raw, member.name)
    cases, evolution = CaseStore(destination/'cases.sqlite'), EvolutionStore(destination/'evolution.sqlite')
    summary, identifiers = [], set()
    try:
        for label, files in sorted(groups.items()):
            documents, evidence, audits, executions = {}, [], [], []
            for name, (raw, member) in sorted(files.items()):
                text = raw.decode('utf-8')
                documents[name] = json.loads(text) if name.endswith('.json') else text
                evidence.append(dict(member=member, sha256=hashlib.sha256(raw).hexdigest(), archive_sha256=archive_hash))
                identifiers.update(re.findall(r'0x[0-9a-fA-F]{40,}', text))
                if name.startswith('ds-audit'):
                    value = documents[name]
                    if 'choices' in value:
                        value = json.loads(value['choices'][0]['message']['content'])
                    audits.append(dict(file=name, result=value))
                if name.startswith('exec-'):
                    executions.append(dict(file=name, result=documents[name]))
            identifiers.add(label)
            notes = documents.get('NOTES.md', '')
            findings = []
            if executions:
                findings.append('receipt_unverified')
            if len(audits)>1 and len({json.dumps(a['result'],sort_keys=True) for a in audits})<len(audits):
                findings.append('evidence_conflict')
            if 'false positive' in notes.lower() or '误报' in notes:
                findings.append('selector_disputed')
            prediction = {'audit_reports': audits}
            outcome = dict(execution_reports=executions, notes_claim=notes,
                           chain_verified=False, status='unverified_history')
            record = dict(label=label, documents=documents, evidence=evidence,
                          verification='archive_only', missing=['verified_runtime_code', 'permissions_snapshot', 'independent_receipt'],
                          prediction=prediction, outcome=outcome, findings=findings)
            key = cases.record_historical(record)
            evolution.observe(case_id=key, prediction=prediction, outcome=outcome, findings=findings,
                              evidence=evidence, verification='archive_only')
            summary.append(dict(case_id=key, label=label, findings=findings, fast_reuse_eligible=False))
        candidates = evolution.propose()
        # Archived claims are discovery material, not independent validation labels.
        fixtures = [dict(case_id=c['case_id'], features={k:True for k in c['findings']},
                         expected_trigger=None, verified=False, split='discovery') for c in summary]
        evaluations = [evolution.evaluate(c['rule_id'], fixtures) for c in candidates]
        report = dict(historical_cases=summary, candidate_rules=candidates, active_rules=evolution.active(),
                      candidate_evaluations=evaluations,
                      fast_reuse_cases=cases.db.execute('SELECT count(*) FROM cases').fetchone()[0],
                      independent_chain_verification=False)
        (destination/'import-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        # Official wallet docs use this public native-token sentinel. It is not
        # a user's wallet/contract identifier; do not misclassify it as private.
        identifiers = {x for x in identifiers if x.lower() != '0x' + 'e'*40}
        (destination/'release-denylist.json').write_text(json.dumps(sorted(identifiers),ensure_ascii=False,indent=2)+'\n')
        for p in destination.iterdir():
            if p.is_file(): p.chmod(0o600)
        return report
    finally:
        cases.close(); evolution.close()


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--archive', required=True)
    parser.add_argument('--private-dir', required=True)
    args=parser.parse_args()
    os.umask(0o077)
    result=import_archive(args.archive,args.private_dir)
    print(json.dumps(dict(imported=len(result['historical_cases']), candidates=len(result['candidate_rules']),
                          fast_reuse=result['fast_reuse_cases'],active_rules=len(result['active_rules']['rules']))))
