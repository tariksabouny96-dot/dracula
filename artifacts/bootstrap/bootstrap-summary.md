# HOOD Bootstrap & Machine Readiness Report

- **Generated**: 2026-10-09T12:06:29.907629+00:00
- **Assigned Role**: `dev-temporary`
- **Host**: `f0cf5039bb2b` (Linux-6.18.44-x86_64-with-glibc2.41)
- **Admin Status**: `Yes`
- **CPU**:  (5 Cores / 5 Threads)
- **RAM**: 5.81 GB
- **Disk C:**: 29.33 GB free of 31.45 GB (93.3% available)
- **GPU**: Unknown

## Dependency Readiness Matrix

| Capability | Required | Status | Detected Version | Path | Smoke Test |
|---|---|---|---|---|---|
| **Git Source Control** | Yes | `PRESERVED_COMPATIBLE` | 2.47.3 | `/usr/bin/git` | `FAIL` |
| **Python 3 Runtime** | Yes | `PRESERVED_COMPATIBLE` | 3.13.5 | `/opt/pyvenv/bin/python` | `PASS` |
| **Python Virtual Environment** | Yes | `PRESERVED_COMPATIBLE` | 3.13.5 | `N/A` | `PASS` |
| **Node.js Runtime** | Yes | `PRESERVED_COMPATIBLE` | 22.16.0 | `/opt/nvm/versions/node/v22.16.0/bin/node` | `PASS` |
| **Node Package Manager** | Yes | `PRESERVED_COMPATIBLE` | 10.9.2 | `/opt/nvm/versions/node/v22.16.0/bin/npm` | `PASS` |
| **Docker / Container Engine** | Optional | `SKIPPED_NOT_REQUIRED` | N/A | `N/A` | `N/A` |
| **Windows Subsystem for Linux (WSL2)** | Optional | `SKIPPED_NOT_REQUIRED` | N/A | `N/A` | `N/A` |
| **PostgreSQL Database Engine** | Optional | `SKIPPED_NOT_REQUIRED` | N/A | `N/A` | `N/A` |
| **pgvector Extension** | Optional | `SKIPPED_NOT_REQUIRED` | N/A | `N/A` | `N/A` |
| **Playwright Automation** | Yes | `MISSING` | N/A | `N/A` | `FAIL` |
| **Secure Secret Vault Backend** | Yes | `PRESERVED_COMPATIBLE` | 1.0.0 | `N/A` | `PASS` |

## Summary & Next Actions

- **Compatible Core Prerequisites**: 6
- **Safely Deferred / Skipped**: 4
- **Missing Required**: 1
- **Failures**: 0

### Readiness Assessment

> [!WARNING]
> **Status: MACHINE_BLOCKED**
> Missing required dependencies: 1
