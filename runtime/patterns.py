"""Learn reusable audit checklists from reviewed source structure, never verdicts.

Lexical retrieval only, not a Solidity compiler or semantic equivalence checker.
Literals/operators are preserved; identifier renaming is abstracted for retrieval.
"""
import json
import re
import sqlite3
from .common import digest

CHECKS={'payment_amount','recipient_binding','external_calls','access_control',
        'supply_limit','proof_binding','upgrade_path','reentrancy'}
KEEP=set('function returns return require assert revert if else for while mapping address uint uint256 bool bytes bytes32 payable external public internal private view pure modifier constructor msg sender value tx origin this super owner onlyOwner nonReentrant transfer transferFrom safeTransferFrom call delegatecall staticcall selfdestruct approve permit mint totalSupply balanceOf'.split())
TOKEN=re.compile(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|0x[0-9a-fA-F]+|\d+(?:\.\d+)?|[A-Za-z_$][\w$]*|==|!=|<=|>=|&&|\|\||=>|\+\+|--|[^\s]')


def shape(source):
    if not isinstance(source,str) or not source.strip() or len(source)>500000:
        raise ValueError('bounded source text required')
    tokens=[]
    for token in TOKEN.findall(source):
        if token.startswith(('//','/*')):continue
        tokens.append('ID' if re.fullmatch(r'[A-Za-z_$][\w$]*',token) and token not in KEEP else token)
    if len(tokens)<12:raise ValueError('source too short for structural retrieval')
    grams=sorted({digest(tokens[i:i+5]) for i in range(len(tokens)-4)})
    return dict(version='lexical-v1',tokens=tokens,grams=grams)


def similarity(a,b):
    x,y=set(a['grams']),set(b['grams'])
    return len(x&y)/len(x|y) if x|y else 0


class PatternStore:
    def __init__(self,path):
        self.db=sqlite3.connect(path)
        self.db.executescript('''CREATE TABLE IF NOT EXISTS audit_patterns(id TEXT PRIMARY KEY, record TEXT, active INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS pattern_evaluations(id TEXT PRIMARY KEY, pattern TEXT, record TEXT);
        CREATE TABLE IF NOT EXISTS pattern_events(id INTEGER PRIMARY KEY AUTOINCREMENT, record TEXT);''')
    def close(self):self.db.close()
    def propose(self,source,checks,provenance):
        if (provenance.get('verified') is not True or not provenance.get('source_ref')
                or not provenance.get('case_id') or not provenance.get('review_ref')):
            raise PermissionError('host verified source/deployment binding and checklist required')
        if not checks or any(x not in CHECKS for x in checks):raise ValueError('unsupported audit checklist')
        # No target address, tx template, personal values, or pass/fail conclusion.
        record=dict(shape=shape(source),checks=sorted(set(checks)),provenance=provenance)
        key=digest(record)
        with self.db:self.db.execute('INSERT OR IGNORE INTO audit_patterns VALUES (?,?,0)',(key,json.dumps(record)))
        return key
    def evaluate(self,key,fixtures):
        row=self.db.execute('SELECT record FROM audit_patterns WHERE id=?',(key,)).fetchone()
        if not row:raise ValueError('unknown pattern')
        pattern=json.loads(row[0]);counts=dict(tp=0,tn=0,fp=0,fn=0,excluded=0);seen=set()
        for f in fixtures:
            candidate=shape(f['source']);identity=digest(candidate)
            if (f.get('verified') is not True or not f.get('review_ref') or f.get('split')!='holdout'
                or type(f.get('expected_match')) is not bool or identity in seen
                or identity==digest(pattern['shape']) or f.get('case_id')==pattern['provenance']['case_id']):
                counts['excluded']+=1;continue
            seen.add(identity);matched=similarity(pattern['shape'],candidate)>=.65;expected=f['expected_match']
            counts['tp' if matched and expected else 'fp' if matched else 'fn' if expected else 'tn']+=1
        passed=counts['tp']>=2 and counts['tn']>=2 and counts['fp']==counts['fn']==0
        result=dict(pattern=key,counts=counts,eligible=passed,dataset_digest=digest(fixtures))
        eid=digest(result)
        with self.db:self.db.execute('INSERT OR IGNORE INTO pattern_evaluations VALUES (?,?,?)',(eid,key,json.dumps(result)))
        return dict(evaluation_id=eid,**result)
    def activate(self,key,evaluation,review):
        self._review(review)
        row=self.db.execute('SELECT record FROM pattern_evaluations WHERE id=? AND pattern=?',(evaluation,key)).fetchone()
        if not row or not json.loads(row[0])['eligible']:raise ValueError('independent holdout evaluation required')
        with self.db:
            self.db.execute('UPDATE audit_patterns SET active=1 WHERE id=?',(key,))
            self.db.execute('INSERT INTO pattern_events(record) VALUES (?)',(json.dumps(dict(pattern=key,action='activate',evaluation=evaluation,review=review)),))
    def disable(self,key,review):
        self._review(review)
        with self.db:
            self.db.execute('UPDATE audit_patterns SET active=0 WHERE id=?',(key,))
            self.db.execute('INSERT INTO pattern_events(record) VALUES (?)',(json.dumps(dict(pattern=key,action='disable',review=review)),))
    def _review(self,review):
        if review.get('approved') is not True or not review.get('reviewer') or not review.get('evidence_ref'):
            raise PermissionError('trusted method review required')
    def match(self,source):
        candidate=shape(source);matches=[]
        for key,raw in self.db.execute('SELECT id,record FROM audit_patterns WHERE active=1'):
            row=json.loads(raw);score=similarity(row['shape'],candidate)
            if score>=.65:
                matches.append(dict(pattern_id=key,similarity=score,checks=row['checks'],
                    added_tokens=sorted(set(candidate['tokens'])-set(row['shape']['tokens'])),
                    removed_tokens=sorted(set(row['shape']['tokens'])-set(candidate['tokens'])),
                    requires_current_contract_audit=True))
        return dict(matches=sorted(matches,key=lambda x:x['similarity'],reverse=True)[:5],
                    execution_authorized=False,kind='audit_method_suggestions')
