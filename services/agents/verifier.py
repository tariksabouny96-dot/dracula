"""Independent verifier: deterministic checks that do not trust any agent's claims.

The verifier is not an LLM. It decides PASS only from observed process results:
1. the application compiles;
2. the engineer's unit tests pass;
3. the QA agent's independent acceptance tests (written from the objective, in
   qa_tests/, which the engineer cannot modify) are present and pass.
A missing or empty test suite is UNVERIFIED, never PASS.
"""
from __future__ import annotations

import re

from .contracts import VerificationDecision, VerificationVerdict
from .sandbox import Workspace, python_cmd

_COLLECTED = re.compile(r"(\d+) passed|(\d+) failed|(\d+) error")


def _count_tests(output: str) -> int:
    total = 0
    for passed, failed, errors in _COLLECTED.findall(output):
        total += int(passed or 0) + int(failed or 0) + int(errors or 0)
    return total


def verify(workspace: Workspace) -> VerificationDecision:
    digest_before = workspace.digest()
    checks = []
    has_app = any(p.startswith("app/") and p.endswith(".py") for p in workspace.listing())
    has_qa = any(p.startswith("qa_tests/") and p.endswith(".py") for p in workspace.listing())
    if not has_app:
        return VerificationDecision(verdict=VerificationVerdict.UNVERIFIED, checks=[],
                                    workspace_sha256=digest_before, reason="No application source under app/")
    compile_check = workspace.run("compile", python_cmd("-m", "compileall", "-q", "app"), timeout=60)
    checks.append(compile_check)
    for name, target in (("engineer_unit_tests", "tests"), ("independent_acceptance_tests", "qa_tests")):
        if not (workspace.root / target).is_dir():
            continue
        result = workspace.run(name, python_cmd("-m", "pytest", "-q", "-p", "no:cacheprovider",
                                                "--rootdir", ".", target), timeout=180)
        result.tests_collected = _count_tests(result.output_tail)
        if result.exit_code == 5:  # pytest: no tests collected
            result.passed = False
        checks.append(result)
    digest_after = workspace.digest()
    if digest_after != digest_before:
        # Tests must not rewrite the code under test while it is being judged.
        return VerificationDecision(verdict=VerificationVerdict.UNVERIFIED, checks=checks,
                                    workspace_sha256=digest_after, reason="Workspace changed during verification")
    qa = next((c for c in checks if c.name == "independent_acceptance_tests"), None)
    if not has_qa or qa is None or not qa.tests_collected:
        return VerificationDecision(verdict=VerificationVerdict.UNVERIFIED, checks=checks,
                                    workspace_sha256=digest_before,
                                    reason="No independent acceptance tests were collected")
    failed = [c.name for c in checks if not c.passed]
    if failed:
        return VerificationDecision(verdict=VerificationVerdict.FAIL, checks=checks,
                                    workspace_sha256=digest_before, reason="Failed checks: " + ", ".join(failed))
    return VerificationDecision(verdict=VerificationVerdict.PASS, checks=checks, workspace_sha256=digest_before,
                                reason="All checks passed")
