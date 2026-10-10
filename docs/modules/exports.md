# Document exports (capability id `artifacts`)

Turns a strict `DocumentSpec` into downloadable documents, stores them in an
owner-scoped registry, and serves them through single-use, hashed download
tokens.

## What is real
- **Spec** (`services/exports/spec.py`): a closed, validated model. Unknown
  fields, control characters and lone surrogates are rejected; hard limits bound
  blocks (2000), rows (5000), columns (50), cells (100k), text (100k chars) and
  total spec size (2 MB). Blocks: heading, paragraph, bullet_list, numbered_list,
  table, code, page_break, plus title/author/date.
- **Renderers** (`services/exports/renderers.py`): `md`, `html` (escaped,
  standalone, dark-mode aware, CSP meta, no scripts), `csv`, `xlsx`
  (openpyxl, bold frozen header, one sheet per table), `pdf` (fpdf2 core font,
  "Page N of M", characters no core font can draw are replaced and counted),
  `docx` (python-docx, Table Grid), and `zip` (bundles the other outputs with a
  SHA-256 `MANIFEST.json`). Spreadsheets defend against formula injection: a cell
  starting with `= + - @ TAB CR` is stored as a quote-prefixed string, never a
  formula.
- **Independent validators** (`services/exports/validators.py`): every output is
  re-opened with a *different* reader (pypdf, python-docx, openpyxl plus a raw
  scan for `<f>` formula elements, an HTML tag allowlist that rejects
  script/active content, csv.reader, zipfile with manifest-hash re-check) and a
  25 MB ceiling. Only outputs that pass are registered; a format that fails is
  reported with its reason.
- **Registry** (`services/artifacts/registry.py`): files written once (O_EXCL,
  fsync, mode 0600) and read with O_NOFOLLOW; rows and reads scoped to an owner
  (another owner's artifact/token is a 404); download tokens are
  `base64url(claims).HMAC-SHA256`, bound to owner + artifact, 1-600 s TTL,
  recorded and used up atomically on first redemption; every download re-hashes
  the file and refuses on mismatch.

## HTTP (all authenticated, CSRF-checked, permission-scoped, owner-isolated)
- `POST /api/exports {spec, formats[], confirm}` — EXECUTE_OBJECTIVE — 201
  complete/partial, 422 when nothing renders, 400 on a bad spec/formats.
- `GET  /api/exports` / `GET /api/exports/<exp_...>` — VIEW_PROJECT_DATA.
- `GET  /api/artifacts` / `GET /api/artifacts/<art_...>` — VIEW_PROJECT_DATA.
- `POST /api/artifacts/<art_...>/token {ttl_seconds}` — VIEW_PROJECT_DATA — 201.
- `GET  /api/artifacts/download/<token>` — VIEW_PROJECT_DATA — verified file;
  the token must have been minted for the authenticated owner.

## Capability state
`register_state_provider("artifacts", ...)` runs a render+validate self-test
(cached 10 min): `available` when every format passes, `degraded` when some do,
`failed` when none.

## What is simulated / not done
- No LLM-compose endpoint yet (generate a spec from a natural-language prompt);
  callers pass a spec. That is a later enhancement on top of this pipeline.
