"""Private audit retrospectives and additive, reviewed rule evolution.

No corpus, model output or learned rule is bundled with the source distribution.
"""
import json
import sqlite3
from .common import digest

CHECKS = {'verify_selector_locally', 'refresh_quote_before_execution',
          'verify_receipt_on_chain', 'resolve_conflicting_evidence'}
SUGGESTIONS = {
    'selector_disputed': ('selector_disputed', 'verify_selector_locally'),
    'quote_changed': ('quote_changed', 'refresh_quote_before_execution'),
    'receipt_unverified': ('receipt_unverified', 'verify_receipt_on_chain'),
    'evidence_conflict': ('evidence_conflict', 'resolve_conflicting_evidence'),
}


class EvolutionStore:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS observations(id TEXT PRIMARY KEY, record TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS rules(id TEXT PRIMARY KEY, record TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS evaluations(id TEXT PRIMARY KEY, rule_id TEXT, record TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS rule_versions(version INTEGER PRIMARY KEY AUTOINCREMENT, record TEXT NOT NULL);
        ''')

    def close(self):
        self.db.close()

    def observe(self, *, case_id, prediction, outcome, findings, evidence, verification):
        if not case_id or not evidence or verification not in {'archive_only', 'independently_verified'}:
            raise ValueError('case provenance and verification required')
        if any(x not in SUGGESTIONS for x in findings):
            raise ValueError('unsupported finding')
        row = dict(case_id=case_id, prediction=prediction, outcome=outcome,
                   findings=sorted(set(findings)), evidence=evidence, verification=verification)
        key = digest(row)
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO observations VALUES (?,?)', (key, json.dumps(row)))
        return key

    def propose(self):
        """Propose additional checks; never rewrite the baseline audit policy."""
        grouped = {}
        for key, raw in self.db.execute('SELECT id,record FROM observations'):
            for finding in json.loads(raw)['findings']:
                grouped.setdefault(finding, []).append(key)
        proposed = []
        with self.db:
            for finding, sources in grouped.items():
                feature, check = SUGGESTIONS[finding]
                row = dict(feature=feature, equals=True, add_check=check, observations=sorted(sources))
                key = digest(row)
                self.db.execute('INSERT OR IGNORE INTO rules VALUES (?,?)', (key, json.dumps(row)))
                proposed.append(dict(rule_id=key, **row))
        return proposed

    def evaluate(self, rule_id, fixtures):
        """Only independently reviewed holdout cases count toward activation.

        fixtures are host-reviewed data, not external self-certified model output.
        """
        row = self.db.execute('SELECT record FROM rules WHERE id=?', (rule_id,)).fetchone()
        if not row:
            raise ValueError('unknown candidate')
        rule = json.loads(row[0])
        source_cases = set()
        for oid in rule['observations']:
            obs = json.loads(self.db.execute('SELECT record FROM observations WHERE id=?', (oid,)).fetchone()[0])
            source_cases.add(obs['case_id'])
        counts = dict(tp=0, tn=0, fp=0, fn=0, unknown=0, excluded=0)
        seen = set()
        for f in fixtures:
            cid = f.get('case_id')
            if (not cid or cid in seen or cid in source_cases or f.get('split') != 'holdout'
                    or f.get('verified') is not True or not f.get('review_ref')
                    or type(f.get('expected_trigger')) is not bool):
                counts['excluded'] += 1
                continue
            seen.add(cid)
            value = f.get('features', {}).get(rule['feature'])
            if type(value) is not bool:
                counts['unknown'] += 1
                continue
            expected = f['expected_trigger']
            counts['tp' if value and expected else 'fp' if value else 'fn' if expected else 'tn'] += 1
        eligible = counts['tp'] >= 2 and counts['tn'] >= 2 and not (counts['fp'] or counts['fn'] or counts['unknown'])
        result = dict(rule_id=rule_id, counts=counts, eligible=eligible,
                      dataset_digest=digest(fixtures), rule_digest=digest(rule))
        key = digest(result)
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO evaluations VALUES (?,?,?)', (key, rule_id, json.dumps(result)))
        return dict(evaluation_id=key, **result)

    def activate(self, rule_id, evaluation_id, review):
        if review.get('approved') is not True or not review.get('reviewer') or not review.get('evidence_ref'):
            raise PermissionError('trusted host rule review required')
        row = self.db.execute('SELECT rule_id,record FROM evaluations WHERE id=?', (evaluation_id,)).fetchone()
        if not row or row[0] != rule_id or not json.loads(row[1])['eligible']:
            raise ValueError('candidate lacks passing independent positive/negative evaluation')
        rule = json.loads(self.db.execute('SELECT record FROM rules WHERE id=?', (rule_id,)).fetchone()[0])
        if rule['add_check'] not in CHECKS:
            raise ValueError('only additive checks allowed')
        current = self.active()
        ids = sorted(set(current['rules'] + [rule_id]))
        return self._version(dict(rules=ids, review=review, evaluation_id=evaluation_id))

    def _version(self, row):
        with self.db:
            cur = self.db.execute('INSERT INTO rule_versions(record) VALUES (?)', (json.dumps(row),))
        return cur.lastrowid

    def active(self):
        row = self.db.execute('SELECT version,record FROM rule_versions ORDER BY version DESC LIMIT 1').fetchone()
        return dict(version=row[0], **json.loads(row[1])) if row else dict(version=0, rules=[])

    def rollback(self, version, review):
        if review.get('approved') is not True or not review.get('evidence_ref') or not review.get('reviewer'):
            raise PermissionError('trusted host review required')
        if version == 0:
            rules = []
        else:
            row = self.db.execute('SELECT record FROM rule_versions WHERE version=?', (version,)).fetchone()
            if not row:
                raise ValueError('unknown version')
            rules = json.loads(row[0])['rules']
        return self._version(dict(rules=rules, rollback_to=version, review=review))

    def checks(self, features):
        checks, unknown = set(), set()
        for key in self.active()['rules']:
            rule = json.loads(self.db.execute('SELECT record FROM rules WHERE id=?', (key,)).fetchone()[0])
            value = features.get(rule['feature'])
            if type(value) is not bool:
                unknown.add(rule['feature'])
                checks.add(rule['add_check'])  # missing evidence cannot skip a check
            elif value is True:
                checks.add(rule['add_check'])
        return dict(additional_checks=sorted(checks), unknown_features=sorted(unknown), execution_authorized=False)
