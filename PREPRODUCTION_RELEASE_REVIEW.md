# HOOD & X — Preproduction acceptance review

**Decision: NO-GO. Not deployed. Not a final release.**

This engineering checkpoint is intended for independent evaluation by Claude, Gemini, and Antigravity. It must not be used as proof of full operational or security readiness.

## What was improved in this pass

- The Systems registry now correctly describes the OpenAI adapter as implemented **source-only**, rather than obsolete `UNAVAILABLE`; it is not presented as an operating live integration.
- Unattached telemetry no longer invents measured API spend, healthy network node state, firewall state, or frontier scores.
- Added a fail-closed preproduction gate that inventories all **26** registered product subsystems and specifies target acceptance evidence for each one.
- Added a local boot smoke test and truthfulness regressions. Runtime starts on loopback and operational APIs do not become public before authentication setup.
- Existing remediations and earlier source are preserved, with no external calls or deployments.

## Execution evidence

Run from the project root in an isolated test environment:

```bash
python scripts/preproduction_gate.py --full-suite --timeout 70
```

The gate *intentionally exits 1* while any acceptance requirement is outstanding; an exit code of zero is not currently possible. Detailed evidence: `artifacts/preproduction_acceptance.json`.

Checks observed in this container on 2026-10-09:

| Check | Result |
|---|---|
| Selected offline hardening, unit, release and preproduction tests | **70 passed** |
| Python compileall | Passed |
| JavaScript syntax: `node --check ui/static/app.js` | Passed |
| Full pytest suite | **199 passed, 17 failed, 17 errors** |
| Verified production subsystems | **0 of 26** (not a claim that none function; means deployment-grade proof absent) |
| Real provider calls and billing | Not tested |
| Windows desktop and microphone | Not tested |
| Authenticated browser acceptance | Not completed |
| Preproduction deployment | Not performed: no approved isolated deployment target and production-grade acceptance incomplete |

Full-suite failures are not all evidence of product defects: some rely on Windows hardware, externally blocked APIs, an absent Chromium bundle, obsolete assertions which expect blocked unsafe behavior, and synchronous Playwright inside an async event loop. They **all remain release blockers** until correctly triaged and independently retested.

## Mandatory preproduction go/no-go

- [ ] Full clean-install regression suite green on supported operating systems; external integration tests explicitly isolated and independently passed in staging.
- [ ] All P0 and applicable P1 audit findings retested against actual execution boundaries.
- [ ] Identity, session, role permissions, X activation/deactivation, emergency stop, tool grants, memory isolation verified end to end.
- [ ] Complete general-purpose agent workflows: actual tasks, independent verification, interrupt/restart recovery and artifact receipts.
- [ ] Connected and verified mail/calendar/e-commerce/freelance workflows in **sandbox** accounts with billing and action approvals.
- [ ] Browser, desktop and real microphone/voice flows verified on target Windows machine.
- [ ] Provider credentials provisioned safely, billing quotas verified and budget enforcement validated with an explicit capped spend authorization.
- [ ] Versioned staging configuration, observability, backup, restore, secrets rotation and deployment rollback verified.
- [ ] UI desktop/mobile accessibility and browser console acceptance completed against the owner's actual reference mockup.
- [ ] Independent security, dependency, supply-chain and AI-agent prompt-injection assessment completed.
- [ ] Target-specific preproduction deployment run and rollback rehearsed with synthetic data.

## Target Windows validation commands

From a fresh working copy with no real production accounts:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q tests\hardening tests\unit tests\release tests\preproduction --ignore=tests\unit\test_bootstrap.py
.\.venv\Scripts\python.exe -m pytest -q tests
.\.venv\Scripts\python.exe scripts\preproduction_gate.py --full-suite
```

Do not point Hood at personal directories or real customers during acceptance. Use fake API accounts, mock servers, empty data, and isolated workspaces. The gate will remain NO-GO until evidence-backed acceptance and a separately reviewed deployment decision are implemented.

## External auditor instructions

Use `artifacts/preproduction_acceptance.json` and original `REMEDIATION_REPORT.md` as an index, not as unquestionable truth. Reconstruct actual flows from code and reproduce each issue. In particular, challenge model routing, tool and shell authority, browser upload confinement, cancellation, X authority, UI status truthfulness, memory tenant isolation, provider spend governance and independent artifact verification. Do not mark a capability tested simply because its module exists or selected unit tests pass.
