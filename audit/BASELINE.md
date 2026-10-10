# Baseline record (Batch 0)

- Source: `HOOD_P0P1_agent_runtime_checkpoint.zip` (zip comment `33c364afba88bc201747daa3e2d0f65c459ad2a9`),
  imported unmodified as commit `faafb93` on branch `claude/hello-mecuky`. The archive had no `.git`, so the
  upstream history and the original F01–F36 audit text were **not** available. Finding titles in
  `FINDINGS_F01_F36.csv` are reconstructed from `REMEDIATION_REPORT.md`; reconcile against the original audit.
- Host: Linux 6.18 (cloud container), Python 3.13.16, Node 22, Chromium 1194 preinstalled
  (pip Playwright 1.63 expects build 1243), no outbound internet except package mirrors, no provider keys.
- Baseline command: `python -m pytest tests -q -p no:cacheprovider -rfEs`
  → **238 passed, 9 failed, 17 errors** (raw log: `audit/baseline/full_suite_baseline.log`).

| Group | Count | Classification at baseline | Outcome on this branch |
|-------|-------|----------------------------|------------------------|
| Browser/dev-executor errors (Playwright build mismatch) | 17 | Missing dependency (browser binary) | Fixed: detect preinstalled Chromium; all run |
| Browser upload Windows-path test | 1 | Code defect (foreign path treated as relative) | Fixed via shared `confine_path` |
| Browser approval-gated test | 1 | Obsolete expectation (insecure generic approval succeeded) | Rewritten to exact-binding contract |
| Voice surface (3) | 3 | Obsolete expectations asserting fabricated audio/free tier | Rewritten to fail-closed contract; code no longer claims free tier |
| Bootstrap idempotency | 1 | Code defect (Windows-only venv path) | Fixed |
| Live Gemini (2), orchestration (1), dev break-fix (1) | 4 | External access (no key) | Marked `live_provider`, skipped as BLOCKED_EXTERNAL |
| Live internet (2) | 2 | External access (no outbound network) | Marked `live_network`, skipped as BLOCKED_EXTERNAL |

`demo_app/` deliberately contains a divide-by-zero defect (negative control); it is outside `testpaths`.

Excluded checks: Windows-native desktop, microphone, firewall; live providers; public internet; staging deployment.
