# Thanatos task notes — `d7baec78-99e0-59bc-97ec-7afa51602798` (`psf/httpbin`)

Source of truth: `Task Creation SOP __ Orion.pdf` (55 pp) + `instru.md` playbook.
Text extract of the SOP: `../SOP.txt`.

## Status board

| Phase | Status | Evidence / notes |
|---|---|---|
| A. Setup and assignment | Done | Local env, Docker, Harbor `0.25.0`, and target GitHub repo (`shivamtiwariethara/thanatos`) verified |
| B. Repository and migration selection | Done | `psf/httpbin` v0.10.4 (`f7b02ae1c6f760a26d08e0e080097e6c9b2cc923`), Flask 3.0.3 -> FastAPI 0.115.6 + Uvicorn 0.32.1 |
| C. Environment and offline closure | Done | `than-httpbin-env:test` + `than-httpbin-tests:test` built offline; split `wheels/orig` and `wheels/target`; `tmux` preinstalled for `terminus-2` |
| D. Instruction | Done | `instruction.md` with 5 required headings, behaviour contract, red lines, scoring |
| E. Oracle/reference solution | Done | `solution/fix.patch` (22 files, LF), `solution/solve.sh`, `solution/TRUTH.md` |
| F. Verifier, tests, judge and reward | Done | `tests/test_outputs.py` (185 probes, 12 groups, hardened static gate), `tests/test.sh`, `tests/judge.toml` (13 criteria, `ollama/than-judge`), `tests/reward.toml` (0.8/0.2) |
| G. Local QC validation (G1–G7) | Done | All gates G1–G7 PASS on rebuilt `than-httpbin-tests:test` with local Ollama judge (`reward = 0.953846` on oracle) |
| H. Model trajectories and difficulty | Done | Harbor `0.25.0` trials complete: `oracle` (`0.954`), `nop` (`0.0`), `terminus-2` via local Ollama `than-judge` (`0.0`, 0 exceptions); `hard` band confirmed |
| I. Metadata and documentation | Done | `task.toml` validated with Harbor 0.25.0 `TaskConfig`; `solution/TRUTH.md` complete |
| J. Peer review and submission | Done | Deliverables committed and pushed to `https://github.com/shivamtiwariethara/thanatos` |

## Environment & image evidence (verified 2026-11-25)

| Check | Result |
|---|---|
| `python --version` (host) | 3.13.17 (containers use Python 3.11.18) |
| `harbor --version` | `0.25.0`; `TaskConfig.model_validate_toml` valid (`schema_version = "1.4"`) |
| `than-httpbin-env:test` | `sha256:ff8c3d4825dd857e0957e780c6c3664926315fb124a905cb984d2cece9386d40` |
| `than-httpbin-tests:test` | `sha256:d27ecc3e5c652b7ae115a9c6f67f3a7d8d2c5fc0c94aae9310398f123ff8cb0f` |
| `test_outputs.py --selfcheck` | `selfcheck ok: 185 probes in 12 groups` |

## What was changed in this handover session

1. **`tasks/d7baec78-99e0-59bc-97ec-7afa51602798/tests/test_outputs.py`**
   - Added `REFERENCE_MARKERS` (`orig-venv`, `requirements-original`, `wheels/orig`, `start-original.sh` in addition to `REFERENCE_PATHS`).
   - Added `_check_symlinks()` to catch file, directory, and broken symlinks pointing to `REFERENCE_MARKERS`, `FORBIDDEN_RE`, or outside `/workspace`.
   - Removed the `TEXT_EXTS` blind spot so extensionless scripts and arbitrary files are scanned (including raw-byte `REFERENCE_MARKERS` checks on binaries).
   - Added `_scan_encoded_tokens()` (base64, urlsafe base64, `\x..` hex escapes, raw hex strings) and `_scan_python_ast()` (constant propagation, `+`, `Path / ...`, `os.path.join`, `"".join(chr(...))`, `b64decode`, `fromhex`, `rot_13`).
   - Added `.zip`/`.whl` archive member scanning in `/workspace` for forbidden/reference entries.
2. **`tasks/d7baec78-99e0-59bc-97ec-7afa51602798/tests/test.sh`**
   - Added `trap 'rm -rf "$WORK"' EXIT` so `/tmp/than-verify-*` is cleaned up on early exits (absent gate / red-line gate).
3. **`tasks/d7baec78-99e0-59bc-97ec-7afa51602798/solution/TRUTH.md`**
   - Created complete private author reference covering task identity, manifest, 12 behaviour groups, checker mapping, red lines, anti-cheat coverage, G1–G7 results, and blockers.
4. **Rebuilt `than-httpbin-tests:test`** after verifier edits.

## G1–G7 validation summary (2026-11-25)

- **G1 (Oracle):** PASS — `solve.sh` exit 0; `REWARDKIT_DISABLE_JUDGE=1 bash /tests/test.sh` exit 0; `70 passed` in pytest; 12/12 groups, 185/185 probes; `behaviour = 1.0`, `reward = 1.0`.
- **G2 (Null/No-op):** PASS — Empty and untouched workspaces both exited 0, wrote `{"reward": 0.0}`, and skipped the judge.
- **G3 (Blindness / Anti-cheat):** PASS — All 12 isolated cases (vendored legacy, renamed vendor, file/dir/no-ext symlinks, `subprocess.Popen(["/opt/original/start.sh"])`, AST `os.path.join`, AST `chr()`, extensionless shell wrapper, base64-encoded, hex-encoded, zip-bundled `flask`) triggered `check_static`, exited 0, wrote `{"reward": 0.0}` with no `reward-details.json`, skipped the judge, and left 0 `/tmp/than-verify-*` dirs.
- **G4 (Identity Check):** PASS — Original vs original via `start_server` / `record_all` / `diff_records`: `G4_TOTAL_PROBES=185 MISMATCHES=0`, 12/12 groups passed.
- **G5 (Determinism):** PASS — Two oracle runs produced byte-identical `reward.json`, `reward-details.json`, `--measure` stdout, `groups`, and `artifact` metadata (`reward = 1.0`).
- **G6 (Leakage & Isolation):** PASS — `than-httpbin-env:test` runs as `agent` (UID 1000), has no `/tests`, `/solution`, or `/workspace/.git`, zero leaked strings in `/workspace`, offline `PIP_NO_INDEX=1`.
- **G7 (Judge Sanity):** PASS — `judge.toml` validated (`ollama/than-judge` via `http://host.docker.internal:11434/v1`, 13 criteria). Missing-key/endpoint guard exited 1 with `verifier error: OPENAI_API_KEY is not set` and wrote no reward file. Live end-to-end judge pipeline verified against local Ollama `than-judge`: oracle scored `judge = 0.769231` (10/13), `behaviour = 1.0`, `reward = 0.953846`.

## Phase H — Model trajectories and difficulty calibration (2026-11-25)

- **Oracle (`harbor run --agent oracle`):** 0 exceptions, `behaviour = 1.000`, `judge = 0.769`, `reward = 0.954`, all 12/12 groups passed.
- **No-op (`harbor run --agent nop`):** 0 exceptions, `reward = 0.0` (absent gate).
- **Local model (`harbor run --agent terminus-2 --model openai/than-judge`, `max_turns = 6`):** 0 exceptions, `trajectory.json` (28,802 bytes), `reward = 0.0` (absent gate; 1.5B local model did not generate FastAPI/Starlette source within 6 turns).
- **Difficulty band:** `hard` (25% to < 37.5%) confirmed.
