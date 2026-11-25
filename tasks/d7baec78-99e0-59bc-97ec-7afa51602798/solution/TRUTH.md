# TRUTH.md — Private Author Reference

> **CONFIDENTIAL:** Private author and reviewer reference for `psf/httpbin-flask-to-fastapi`. Never expose this file or its contents to the agent container or `/workspace`.

## 1. Task Identity

- **Task Name:** `psf/httpbin-flask-to-fastapi`
- **Task UUID (v5):** `d7baec78-99e0-59bc-97ec-7afa51602798`
- **Category:** `web-framework-migration`
- **Schema Version:** `1.4` (`task.toml` validated with Harbor `0.25.0` `TaskConfig`)
- **Declared Difficulty Band:** `hard` (25% to < 37.5%; calibrated via Harbor `0.25.0` oracle/nop/local-model trajectory runs and whole-repo surface analysis)
- **Evaluation Version:** `web-framework-migration-2026-11-25`

## 2. Upstream Repository & Commits

- **Repository:** `https://github.com/psf/httpbin` (`httpbin v0.10.4`)
- **Base Commit:** `f7b02ae1c6f760a26d08e0e080097e6c9b2cc923`
- **Reference Commit:** `7edac6f18dff2e602850cf2053529c7e83067b8c` (local reference commit = base + `solution/fix.patch`; not yet pushed to a remote fork)

## 3. Migration Scope & Stack Versions

- **Source Stack:** Python 3.11, `Flask 3.0.3`, `Werkzeug 3.0.6`, `gunicorn 23.0.0` (with `flasgger` template residue removed)
- **Target Stack:** Python 3.11, `FastAPI 0.115.6`, `uvicorn 0.32.1`, `starlette 0.41.3`, `jinja2 3.1.6`, `python-multipart 0.0.34`, `brotlicffi 1.2.1.0`
- **Test Dependencies (offline):** `pytest 9.2.0`, `httpx 0.28.1`
- **Scope:** Whole-repository migration across `httpbin/`, packaging/process configuration (`pyproject.toml`, `Dockerfile`, `Procfile`, `start.sh`, `httpbin.bash`, `runtime.txt`, `app.json`), CI workflow (`.github/workflows/ci.yml`), `README.md`, and `tests/test_httpbin.py`.

## 4. Deliverable Manifest (`solution/fix.patch`)

`solution/fix.patch` modifies 22 files total (5 added, 16 modified, 1 deleted; pure LF line endings):

| Status | Lines (`+`/`-`) | Path | Role |
|---|---|---|---|
| Added | `+1041 / -0` | `httpbin/app.py` | FastAPI application, all route handlers, CORS/method/error handling |
| Added | `+437 / -0` | `httpbin/compat.py` | Wire-compatibility helpers (JSON formatting, headers, cookies, redirects, IRI/percent-encoding) |
| Added | `+66 / -0` | `httpbin/constants.py` | Static status descriptions and original payload constants |
| Added | `+191 / -0` | `httpbin/testing.py` | ASGI test client adapter for `tests/test_httpbin.py` |
| Added | `+7 / -0` | `start.sh` | Uvicorn startup script (`python3 -m uvicorn httpbin:app`) |
| Modified | `+3 / -1` | `httpbin/__init__.py` | Exposes `app` alongside package exports |
| Modified | `+3 / -1794` | `httpbin/core.py` | Compatibility re-export of `app` (all Flask routes/handlers removed) |
| Modified | `+16 / -98` | `httpbin/filters.py` | Compression filters (`gzip`, `deflate`, `brotli`) migrated to Starlette/FastAPI |
| Modified | `+358 / -395` | `httpbin/helpers.py` | Request/response helpers migrated off Werkzeug/Flask |
| Modified | `+14 / -0` | `httpbin/structures.py` | Case-insensitive header/dict compatibility helpers |
| Modified | `+21 / -0` | `httpbin/utils.py` | Target-stack utility helpers |
| Deleted | `+0 / -209` | `httpbin/templates/flasgger/index.html` | Legacy Flasgger template removed |
| Modified | `+2 / -2` | `httpbin/templates/httpbin.1.html` | Updated stack references in template |
| Modified | `+9 / -15` | `pyproject.toml` | Pins `fastapi==0.115.6`, declares `uvicorn==0.32.1`, removes Flask/Werkzeug/gunicorn/flasgger/gevent |
| Modified | `+6 / -23` | `Dockerfile` | Target Uvicorn build/runtime |
| Modified | `+1 / -1` | `Procfile` | Target Uvicorn process command |
| Modified | `+3 / -3` | `httpbin.bash` | Target Uvicorn launcher |
| Modified | `+3 / -3` | `app.json` | Updated runtime description |
| Modified | `+1 / -1` | `runtime.txt` | Python runtime declaration |
| Modified | `+3 / -3` | `README.md` | Updated stack/runtime documentation |
| Modified | `+3 / -19` | `.github/workflows/ci.yml` | Updated CI install/test steps |
| Modified | `+23 / -47` | `tests/test_httpbin.py` | Migrated test suite (70 tests; `test_index_serves_legacy_landing_page` replaces Flasgger-named test; chunked POST asserts 200 echo) |

## 5. Behaviour Contract & 12 Groups (185 Probes)

Differential verification compares the live pristine original (`/opt/original/start.sh` via gunicorn `127.0.0.1:8080`) sequentially against the built delivery (`python -m uvicorn httpbin:app --host 127.0.0.1 --port 8080`).

| Group | Probes | Coverage |
|---|---:|---|
| `introspection` | 12 | `/`, `/html`, `/robots.txt`, `/json`, `/xml`, `/forms/post`, `/deny`, `/encoding/utf8`, `/links/*` |
| `request_echo` | 17 | `/get`, `/headers`, `/user-agent`, `/ip`, `/anything*`, query/header/Host/Origin/X-Forwarded-* echoes |
| `payload_echo` | 20 | `/post`, `/put`, `/patch`, `/delete`, `/anything` (form, raw, binary, JSON, multipart, chunked, empty) |
| `status_codes` | 22 | `/status/<code>`, deterministic weighted selection, invalid codes/weights, HEAD |
| `redirects` | 14 | `/redirect/*`, `/relative-redirect/*`, `/absolute-redirect/*`, `/redirect-to` (301/307/308/200/missing) |
| `cookies` | 9 | `/cookies`, `show_env`, `/cookies/set`, `/cookies/set/{k}/{v}`, `/cookies/delete`, quoted/multi |
| `auth` | 13 | `/basic-auth/*`, `/hidden-basic-auth/*`, `/bearer`, `/digest-auth/*` (MD5/SHA-256, `auth`/`auth-int`, bad password) |
| `compression` | 8 | `/gzip`, `/deflate`, `/brotli`, `Accept-Encoding` negotiation |
| `content_negotiation` | 11 | `/image`, `/image/{png,jpeg,webp,svg}`, `Accept` q-values, 406, invalid format, HEAD |
| `streaming_ranges` | 21 | `/bytes/*`, `/stream/*`, `/stream-bytes/*`, `/range/*`, `/drip`, `/delay/*` |
| `response_headers_cache` | 17 | `/response-headers`, `/cache`, `/cache/{s}`, `/etag/{etag}` (`If-None-Match`, `If-Match`, weak/`*`/list) |
| `protocol_errors_cors` | 21 | 404/405/`Allow`, `OPTIONS`/`HEAD`/`TRACE`, trailing slash, CORS preflight/origin/credentials/error, `/base64/*` |

**Normalised runtime/transport variance (documented in `instruction.md`):**
- Dropped transport headers: `date`, `server`, `connection`, `transfer-encoding`, `keep-alive`, `content-length` (except `content-length` on `HEAD` requests, which is compared).
- `Allow` header token order is sorted; `WWW-Authenticate` `nonce`/`opaque`/`stale` folded; `Set-Cookie` `stale_after`/`last_nonce`/`expires` folded; `Last-Modified` and random 32-hex `ETag` folded; `/uuid` and 32-hex random tokens folded; loopback host/port (`127.0.0.1`/`172.17.0.1`/`localhost`:`8080`/`18080`/`18081`) folded; doc prose `Gunicorn`/`gunicorn`/`WSGI` -> `Uvicorn`/`uvicorn`/`ASGI`.

## 6. Checker-to-Requirement Mapping

| Requirement (`instruction.md`) | Automated / Judge Check |
|---|---|
| Absent migration yields 0.0 without judge | `test_outputs.py --absent-gate` (exit 3) -> `test.sh` writes `{"reward": 0.0}` |
| Remove `flask`, `werkzeug`, `flasgger`, `gunicorn`, `gevent` & `flasgger/` | `check_static()`, `inspect_artifact()`, `check_installed()`, `judge.toml` `IF-01`, `IF-05`, `CQ-03` |
| No wrapper/symlink/exec/indirect ref to `/opt/original`, `/opt/orig-venv`, `/opt/requirements-original`, `/opt/wheels/orig` | `check_static()` (`_check_symlinks`, raw bytes, collapsed literals, `_scan_encoded_tokens`, `_scan_python_ast`, zip/whl scan), `judge.toml` `IF-06` |
| Pin `fastapi==0.115.6` and declare `uvicorn`; offline wheel build | `check_static()`, `build_delivery()` (`pip install --no-index --find-links /opt/wheels/target`), `inspect_artifact()`, `judge.toml` `IF-04` |
| Retain `tests/test_httpbin.py` (`HttpbinTestCase`, 70 tests, no skips) | `check_static()` test-name diff + `measure()` pytest runner check, `judge.toml` `IF-03` |
| Preserve all 12 HTTP behaviour groups | `run_differential()` (185 probes vs live original), `judge.toml` `IF-02` |
| Idiomatic FastAPI/Starlette & clean wiring | `judge.toml` `CQ-01`, `CQ-02`, `CQ-04` |
| Agent process & isolation | `judge.toml` `P-01`, `P-02`, `P-03` |

## 7. Golden Solution & Build/Runtime Preconditions

- **Apply golden solution:** `bash solution/solve.sh` (runs `git apply --binary "$here/fix.patch"` in `${TASK_WORKSPACE:-/workspace}`).
- **Build & test preconditions:**
  - `PIP_NO_INDEX=1`, `PIP_FIND_LINKS=/opt/wheels/target`, `--no-build-isolation`
  - Unprivileged execution as `agent` (`UID 1000`, `GID 1000`)
  - Runtime command: `python -m uvicorn httpbin:app --host 127.0.0.1 --port 8080`
  - Sequential execution of original (`gunicorn`) and delivery (`uvicorn`) on `127.0.0.1:8080`.

## 8. Migration Red Lines & Anti-Cheat Coverage

Any red line forces `gate = false`, `reward = 0.0`, and exits before build, differential measurement, or judge invocation:
1. Missing `pyproject.toml`, no `.py` files, no `fastapi`/`starlette` import, or forbidden dependency in build metadata.
2. Any path or file content matching whole-word `flask|werkzeug|flasgger|gunicorn|gevent` (case-insensitive), or `httpbin/templates/flasgger/` present.
3. Any symlink (file, directory, or broken) pointing to `REFERENCE_MARKERS` or escaping `/workspace`.
4. Any file (including extensionless scripts and binary files) containing literal, whitespace/quote-concatenated, Python AST-folded (`os.path.join`, `Path / ...`, `"".join(chr(...))`, variable propagation), base64-encoded, or hex-encoded references to `/opt/original`, `/opt/orig-venv`, `/opt/requirements-original`, `/opt/wheels/orig`, `orig-venv`, `requirements-original`, `wheels/orig`, or `start-original.sh`.
5. Any `.zip` or `.whl` in `/workspace` containing forbidden stack paths.
6. Offline build failure, missing wheel, forbidden package in built wheel or installed `site-packages`, missing `fastapi` pin, or unexpected top-level package.
7. Missing/renamed/skipped tests in `tests/test_httpbin.py`.

**Rejected routes and detection:**
- *Vendored/renamed Flask or Werkzeug module:* caught by `FORBIDDEN_RE` in `check_static` (and `inspect_artifact`/`check_installed`).
- *Symlink to `/opt/original` (file, dir, or extensionless):* caught by `_check_symlinks` in `check_static`.
- *Python `subprocess.Popen` or shell wrapper calling `/opt/original/start.sh`:* caught by raw-byte + text `REFERENCE_MARKERS` scan in `check_static` (and UID 1000 `chmod go-rwx` permission barrier at runtime).
- *Indirect path assembly (`os.path.join`, `Path / ...`, `chr()`, base64, hex):* caught by `_scan_encoded_tokens` and `_scan_python_ast` in `check_static`.
- *Bundled `.zip`/`.whl` with `flask`:* caught by archive member scan in `check_static`.

## 9. G1–G7 Validation & Phase H Results (Verified 2026-11-25)

All checks below were executed on the final task bytes with `than-httpbin-env:test` (`sha256:ff8c3d4825dd857e0957e780c6c3664926315fb124a905cb984d2cece9386d40`) and `than-httpbin-tests:test` (`sha256:d27ecc3e5c652b7ae115a9c6f67f3a7d8d2c5fc0c94aae9310398f123ff8cb0f`):

| Gate / Phase | Status | Evidence |
|---|---|---|
| **G1 (Oracle)** | **PASS** | `solve.sh` exit 0; `70 passed` in pytest; 12/12 groups, 185/185 probes matched; `behaviour = 1.0`. |
| **G2 (Null / No-op)** | **PASS** | Empty `/workspace` and untouched `/workspace` both returned `absent = true` (exit 3 on `--absent-gate`), wrote `{"reward": 0.0}`, exited 0, and skipped judge. |
| **G3 (Blindness / Anti-cheat)** | **PASS** | 12/12 isolated cases (vendored, renamed vendor, file/dir/no-ext symlinks, Python `subprocess.Popen` literal, AST `os.path.join`, AST `chr()`, shell wrapper without extension, base64-encoded, hex-encoded, zip-bundled `flask`) all triggered `check_static` (`gate: false`), exited 0, wrote `{"reward": 0.0}` with no `reward-details.json`, skipped judge, and left 0 `/tmp/than-verify-*` dirs. |
| **G4 (Identity Check)** | **PASS** | Original service run as both reference and candidate via `start_server`/`record_all`/`diff_records`: 185/185 probes, 0 mismatches, 12/12 groups passed. |
| **G5 (Determinism)** | **PASS** | Two consecutive oracle runs produced byte-identical `reward.json`, `reward-details.json`, `--measure` stdout, `groups`, and `artifact` metadata (`behaviour = 1.0`, `reward = 1.0`). |
| **G6 (Leakage & Isolation)** | **PASS** | `than-httpbin-env:test` runs as UID 1000 (`agent`), has no `/tests`, no `/solution`, no `/workspace/.git`, no leaked verifier/solution strings in `/workspace`, and `PIP_NO_INDEX=1`. `task.toml` restricts agent network to `["127.0.0.1"]`. |
| **G7 (Judge Sanity)** | **PASS** | `judge.toml` validated (`ollama/than-judge` via `http://host.docker.internal:11434/v1`, 13 criteria: `IF-01..06`, `CQ-01..04`, `P-01..03`). Missing-key/endpoint guard exits 1 with `verifier error: OPENAI_API_KEY is not set` and writes no reward file. Live end-to-end judge pipeline verified against local Ollama `than-judge` (`qwen2.5:1.5b` base): oracle scored `judge = 0.769231` (10/13 criteria), `behaviour = 1.0`, `reward = 0.953846`. |
| **Phase H (Trajectories & Calibration)** | **PASS** | Harbor `0.25.0` trials on final task bytes: `oracle` (`0` exceptions, `behaviour = 1.000`, `judge = 0.769`, `reward = 0.954`); `nop` (`0` exceptions, `reward = 0.0`); `terminus-2` with local Ollama `than-judge` (`max_turns = 6`, `0` exceptions, `trajectory.json` 28,802 bytes, `reward = 0.0` via absent gate). Difficulty band `hard` confirmed. |

## 10. Tooling & Environment Notes

- **Local Free Judge (`ollama/than-judge`):** `tests/judge.toml` and `task.toml` `[verifier.env]` are configured to use `ollama/than-judge` at `http://host.docker.internal:11434/v1` (with `host.docker.internal` in `[verifier]` `allowed_hosts`), enabling zero-cost offline-compatible judge evaluation without external API keys. When `base_url` is set, `run_judge()` compacts the diff manifest and retries transient connection drops.
- **Harbor `reward.json` Compatibility:** `score()` writes strictly numeric fields (`reward`, `behaviour`, and `judge` when enabled) to `reward.json` for Harbor `0.25.0` `_parse_reward_json()`, and writes full `gate`, `groups`, and `judge_answers` metadata to `reward-details.json`.
- **Tooling Note:** Local `harbor --version` is `0.25.0`; `RewardKit 0.2.3` was unavailable on PyPI, so scoring/blending (`0.8 * behaviour + 0.2 * judge`) and OpenAI-compatible judge calls are implemented directly in `tests/test_outputs.py`.

## 11. Reproducibility Commands

```powershell
# Build agent and verifier images
docker build -t than-httpbin-env:test tasks\d7baec78-99e0-59bc-97ec-7afa51602798\environment
docker build -t than-httpbin-tests:test tasks\d7baec78-99e0-59bc-97ec-7afa51602798\tests

# Selfcheck
python tasks\d7baec78-99e0-59bc-97ec-7afa51602798\tests\test_outputs.py --selfcheck
```
