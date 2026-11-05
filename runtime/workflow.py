"""Glue for host-controlled evidence checks, vendor-neutral agents, and cleanup."""
from .research_method import require_target


class Workflow:
    def __init__(self, router, cases, cleanup, evolution=None, patterns=None):
        self.router, self.cases, self.cleanup = router, cases, cleanup
        self.evolution = evolution
        self.patterns = patterns

    def research(self, task_id, evidence):
        return self.router.call(task_id, 'research', {'evidence': evidence})

    def audit(self, task_id, snapshot, evidence, features=None, *, resolution=None):
        require_target(snapshot, resolution)
        # Do not send the entire case database or chat history to a model.
        match = self.cases.match(snapshot)
        payload = {'snapshot': snapshot, 'evidence': evidence, 'case_match': match}
        if self.evolution:
            payload['learned_checks'] = self.evolution.checks(features or {})
        return self.router.call(task_id, 'audit', payload)

    def learn(self, snapshot, audit_result, host, outcome=None, source_material=None):
        """Persist/classify every completed reviewed audit, including failures."""
        review = host.review_audit(snapshot, audit_result)
        case_id=self.cases.record(snapshot, audit_result, review, outcome)
        if self.patterns and review.get('verified') is True:
            material=None
            if source_material and source_material.get('verified') is True and audit_result.get('method_checks'):
                material={**source_material,'checks':audit_result['method_checks']}
            elif hasattr(host,'pattern_material'):
                material=host.pattern_material(snapshot,audit_result)
            if material is not None:
                self.patterns.propose(material['source'],material['checks'],dict(
                    verified=material.get('verified') is True,source_ref=material.get('source_ref'),
                    case_id=case_id,review_ref=review['evidence_ref']))
        return case_id
