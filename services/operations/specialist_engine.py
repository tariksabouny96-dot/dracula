"""Deterministic, isolated local specialists with independently reproducible receipts.

No models, tools, browser, shell or network. Outputs are advisory, never authorization.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


@dataclass(frozen=True)
class Specialist:
    name: str
    version: str
    function: object


def deliverables(objective: str):
    """Extract candidate deliverables; no statement of project completion."""
    segments = re.split(r'[\n;.!?]+', objective)
    segments = [re.sub(r'\s+', ' ', part).strip() for part in segments]
    segments = [part[:240] for part in segments if len(part) >= 8][:12]
    return {'candidates': segments, 'count': len(segments),
            'interpretation': 'HEURISTIC_UNVERIFIED', 'requires_human_review': True}


def safety_review(objective: str):
    """Conservative lexical signals. This is not a security classifier."""
    patterns = {
        'financial_action': r'\b(pay|payment|purchase|invoice|charge|transfer|buy|sell)\b',
        'external_publication': r'\b(publish|deploy|send|email|post|upload|broadcast)\b',
        'system_modification': r'\b(delete|overwrite|execute|install|migrate|shutdown|reset)\b',
        'credential_access': r'\b(password|credential|secret|token|api key|private key)\b',
    }
    flags = [name for name, pattern in patterns.items() if re.search(pattern, objective, flags=re.I)]
    return {'signals': flags, 'further_approval_required': bool(flags),
            'coverage': 'KEYWORD_ONLY_NOT_A_SECURITY_GUARANTEE',
            'permitted_external_actions': []}


SPECIALISTS = (
    Specialist('deliverable_analyst', '1', deliverables),
    Specialist('boundary_reviewer', '1', safety_review),
)


def review(objective: str, plan_hash: str):
    if not isinstance(objective, str) or not 10 <= len(objective.strip()) <= 8000:
        raise ValueError('Invalid mission objective')
    if not isinstance(plan_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', plan_hash):
        raise ValueError('Invalid approved plan digest')
    jobs = []
    for specialist in SPECIALISTS:
        output = specialist.function(objective)
        jobs.append({'specialist': specialist.name, 'version': specialist.version,
                     'status': 'COMPLETED', 'result': output, 'result_sha256': digest(output)})
    # Independently recompute each result, do not trust a self-attested verified flag.
    checks = []
    for job, specialist in zip(jobs, SPECIALISTS):
        recalculated = specialist.function(objective)
        checks.append(job['specialist'] == specialist.name and
                      job['result_sha256'] == digest(recalculated) and job['result'] == recalculated)
    if not all(checks):
        raise ValueError('Specialist verification failed')
    return {'schema_version': 1, 'execution_mode': 'DETERMINISTIC_SPECIALIST_REVIEW',
            'source_plan_sha256': plan_hash, 'specialists': jobs,
            'verification': {'type': 'INDEPENDENT_DETERMINISTIC_RECOMPUTATION',
                             'passed': all(checks), 'checks': checks},
            'advisory_only': True, 'objective_completed': False,
            'llm_agents_executed': False, 'external_actions': False}
