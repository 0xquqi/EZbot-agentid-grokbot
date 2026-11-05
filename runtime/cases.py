"""Local reviewed cases, automatic classification, and conservative reuse routing."""
import json
import sqlite3
from datetime import datetime, timezone
from .common import address, digest

RECHECK = ['chain_and_target', 'runtime_and_implementation', 'owner_and_permissions',
           'external_dependencies', 'payment_path', 'price_and_quantity',
           'eligibility_and_remaining', 'open_and_paused', 'simulation', 'wallet_preview']


def classify(snapshot):
    selectors = set(snapshot.get('selectors', []))
    tags = ['proxy' if snapshot['proxy_kind'] != 'none' else 'direct']
    if '095ea7b3' in selectors:
        tags.append('erc20-like')
    if 'a22cb465' in selectors:
        tags.append('operator-approval-capable')
    if selectors & {'1249c58b', 'a0712d68', '40c10f19', '2db11544'}:
        tags.append('mint-like')
    if selectors & {'4e71d92d', '379607f5'}:
        tags.append('claim-like')
    return tags  # Classification is a search hint, never proof of a function's meaning.


def validate_snapshot(s):
    if type(s.get('chain_id')) is not int or s['chain_id'] <= 0:
        raise ValueError('chain required')
    address(s['address'])
    if s.get('proxy_kind') not in {'none', 'eip1967', 'beacon', 'other', 'unknown'}:
        raise ValueError('explicit proxy classification required')
    for key in ['runtime_sha256', 'implementation_sha256']:
        v = s.get(key)
        if key == 'implementation_sha256' and v is None:
            continue
        import re
        if not isinstance(v, str) or not re.fullmatch('[a-f0-9]{64}', v):
            raise ValueError('full code hash required')
    if not isinstance(s.get('selectors'), list):
        raise ValueError('selectors required')
    if not isinstance(s.get('context'), dict) or not s['context']:
        raise ValueError('permissions/payment/dependency context required')
    if not {'permissions', 'payment_path', 'dependencies'} <= s['context'].keys():
        raise ValueError('incomplete context')


class CaseStore:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS contract_reviews (chain INTEGER, address TEXT, blocked INTEGER, record TEXT, PRIMARY KEY(chain,address))')
        self.db.execute('CREATE TABLE IF NOT EXISTS contract_review_events (id INTEGER PRIMARY KEY AUTOINCREMENT, record TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS cases (id TEXT PRIMARY KEY, record TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS outcome_reviews(case_id TEXT PRIMARY KEY, evidence_digest TEXT, review TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS outcomes (id TEXT PRIMARY KEY, case_id TEXT NOT NULL, record TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS historical_cases (id TEXT PRIMARY KEY, record TEXT NOT NULL)')

    def set_contract_block(self,chain,target,blocked,reason,review):
        if type(chain) is not int or chain<=0 or type(blocked) is not bool or not reason:
            raise ValueError('explicit chain/target/state/reason required')
        if review.get('approved') is not True or not review.get('reviewer') or not review.get('evidence_ref'):
            raise PermissionError('trusted contract review required')
        target=address(target);record=dict(chain=chain,address=target,blocked=blocked,reason=reason,review=review)
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO contract_reviews VALUES (?,?,?,?)',(chain,target,int(blocked),json.dumps(record)))
            self.db.execute('INSERT INTO contract_review_events(record) VALUES (?)',(json.dumps(record),))
    def blocked(self,chain,target):
        row=self.db.execute('SELECT blocked FROM contract_reviews WHERE chain=? AND address=?',(chain,address(target))).fetchone()
        return bool(row and row[0])
    def require_case(self,case_id):
        row=self.db.execute('SELECT record FROM cases WHERE id=?',(case_id,)).fetchone()
        if not row:raise PermissionError('unknown reviewed contract audit')
        record=json.loads(row[0]);s=record['snapshot']
        if self.blocked(s['chain_id'],s['address']):raise PermissionError('contract audit withdrawn')
        if record['review'].get('verified') is not True or record['audit']['verdict']!='pass':
            raise PermissionError('contract lacks reviewed passing audit')
        if self.match(s)['route']!='reuse_static_analysis':
            raise PermissionError('current reviewed evidence conflicts with old passing audit')
        return record

    def record_historical(self, record):
        """Archive material lacks a verified code snapshot; never create a fake one."""
        if record.get('verification') != 'archive_only' or not record.get('evidence'):
            raise ValueError('explicit archive-only provenance required')
        key = digest(record)
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO historical_cases VALUES (?,?)', (key, json.dumps(record)))
        return key

    def historical(self):
        return [dict(case_id=key, **json.loads(raw)) for key,raw in
                self.db.execute('SELECT id,record FROM historical_cases ORDER BY id')]

    def close(self):
        self.db.close()

    def record_outcome(self, case_id, outcome):
        if not self.db.execute('SELECT 1 FROM cases WHERE id=?', (case_id,)).fetchone():
            raise ValueError('unknown case')
        if not outcome.get('evidence_ref') or outcome.get('status') not in {'confirmed', 'reverted', 'aborted', 'unknown'}:
            raise ValueError('verified outcome reference and explicit status required')
        event = dict(case_id=case_id, outcome=outcome)
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO outcomes VALUES (?,?,?)', (digest(event), case_id, json.dumps(event)))

    def _negative_outcomes(self,case_id):
        rows=self.db.execute('SELECT id,record FROM outcomes WHERE case_id=? ORDER BY id',(case_id,)).fetchall()
        return [key for key,raw in rows if json.loads(raw)['outcome']['status']=='reverted']

    def outcome_review_needed(self,case_id):
        negative=self._negative_outcomes(case_id)
        if not negative:return False
        row=self.db.execute('SELECT evidence_digest FROM outcome_reviews WHERE case_id=?',(case_id,)).fetchone()
        return not row or row[0]!=digest(negative)

    def review_outcomes(self,case_id,review):
        if review.get('approved') is not True or not review.get('reviewer') or not review.get('evidence_ref') or not review.get('reason'):
            raise PermissionError('independent failure-cause review required')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            negative=self._negative_outcomes(case_id)
            if not negative:raise ValueError('no reverted outcomes to review')
            self.db.execute('INSERT OR REPLACE INTO outcome_reviews VALUES (?,?,?)',(case_id,digest(negative),json.dumps(review)))

    def record(self, snapshot, audit, review, outcome=None):
        """Only the trusted host calls this after independent evidence review.

        review is NOT taken from an external agent's response. Unreviewed records
        remain candidates and can never enter the fast reuse path.
        """
        validate_snapshot(snapshot)
        if 'mint_selectors' in audit:
            import re
            if not isinstance(audit['mint_selectors'],list) or any(not isinstance(x,str) or not re.fullmatch('[0-9a-f]{8}',x) or x not in snapshot['selectors'] for x in audit['mint_selectors']):
                raise ValueError('audited mint selectors must belong to snapshot')
        if audit.get('verdict') not in {'pass', 'fail', 'unknown'}:
            raise ValueError('invalid verdict')
        if not isinstance(audit.get('evidence'), list) or not audit['evidence']:
            raise ValueError('audit evidence required')
        if not isinstance(audit.get('risks'), list):
            raise ValueError('risks required')
        if not review.get('reviewer') or not review.get('evidence_ref'):
            raise ValueError('host review provenance required')
        row = dict(snapshot=snapshot, audit=audit, review=review, outcome=outcome,
                   categories=classify(snapshot), recorded_at=datetime.now(timezone.utc).isoformat())
        row['id'] = digest(row)
        with self.db:
            self.db.execute('INSERT INTO cases VALUES (?, ?)', (row['id'], json.dumps(row)))
        return row['id']

    def match(self, current):
        validate_snapshot(current)
        if self.blocked(current['chain_id'],current['address']):
            return dict(route='contract_blocked',candidates=[],categories=classify(current),required_rechecks=RECHECK,execution_authorized=False)
        ranked = []
        for (raw,) in self.db.execute('SELECT record FROM cases'):
            row = json.loads(raw)
            s = row['snapshot']
            if s['chain_id'] != current['chain_id'] or self.blocked(s['chain_id'],s['address']):
                continue
            same_code = (s['runtime_sha256'] == current['runtime_sha256'] and
                         s.get('implementation_sha256') == current.get('implementation_sha256') and
                         s['proxy_kind'] == current['proxy_kind'])
            known_proxy = current['proxy_kind'] == 'none' or (
                current['proxy_kind'] in {'eip1967', 'beacon'} and current.get('implementation_sha256'))
            same_context = digest(s['context']) == digest(current['context'])
            a, b = set(s['selectors']), set(current['selectors'])
            similarity = len(a & b) / len(a | b) if a | b else 0
            if not same_code and similarity < .6:
                continue
            reviewed = row['review'].get('verified') is True
            same_target = address(s['address'])==address(current['address'])
            reuse = bool(same_target and same_code and same_context and known_proxy and reviewed and row['audit']['verdict'] == 'pass' and bool(row['audit'].get('mint_selectors')))
            ranked.append(dict(case_id=row['id'], route='reuse_static_analysis' if reuse else 'difference_review',
                               same_code=same_code, same_context=same_context, same_target=same_target, similarity=similarity,
                               verdict=row['audit']['verdict'], risks=row['audit']['risks'],
                               reviewed=reviewed, recorded_at=row['recorded_at'],
                               static_findings=row['audit'].get('static_findings', []),
                               evidence=row['audit']['evidence']))
        # A reviewed failure for the same code blocks fast routing even if a pass exists.
        conflict = any(x['same_target'] and x['same_code'] and x['reviewed'] and x['verdict'] != 'pass' for x in ranked)
        ranked.sort(key=lambda x: (x['same_target'], x['same_code'], x['same_context'], x['recorded_at']), reverse=True)
        route = ranked[0]['route'] if ranked else 'full_audit'
        if any(x['same_target'] and x['same_code'] and self.outcome_review_needed(x['case_id']) for x in ranked):
            route='outcome_review'
        if conflict:
            route = 'conflict_review'
        return dict(route=route, candidates=ranked[:5], categories=classify(current),
                    required_rechecks=RECHECK, execution_authorized=False)
