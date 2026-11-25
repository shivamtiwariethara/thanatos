# Thanatos task notes — `d7baec78-99e0-59bc-97ec-7afa51602798` (`psf/httpbin`)

Source of truth: `Task Creation SOP __ Orion.pdf` (55 pp) + `instru.md` playbook.
Text extract of the SOP: `../SOP.txt`.

## Status board

| Phase | Status | Evidence / notes |
|---|---|---|
| A. Setup and assignment | Partial | Local env verified; blocked on project repo URL, lead track/band confirmation |
| B. Repository and migration selection | Done | `psf/httpbin` v0.10.4 (`f7b02ae1c6f760a26d08e0e080097e6c9b2cc923`), Flask 3.0.3 -> FastAPI 0.115.6 + Uvicorn 0.32.1 |
| C. Environment and offline closure | Done | `than-httpbin-env:test` + `than-httpbin-tests:test` built offline; split `wheels/orig` and `wheels/target` |
| D. Instruction | Done | `instruction.md` with 5 required headings, behaviour contract, red lines, scoring |
| E. Oracle/reference solution | Done | `solution/fix.patch` (22 files, LF), `solution/solve.sh`, `solution/TRUTH.md` |
| F. Verifier, tests, judge and reward | Done | `tests/test_outputs.py` (185 probes, 12 groups, hardened static gate), `tests/test.sh`, `tests/judge.toml` (13 criteria), `tests/reward.toml` (0.8/0.2) |
| G. Local QC validation (G1–G7) | G1–G6 PASS, G7 Blocked (live API) | All locally runnable gates verified 2026-11-25 on rebuilt `than-httpbin-tests:test` |
| H. Model trajectories and difficulty | Blocked | No model/judge API credentials in environment; `hard` band uncalibrated |
| I. Metadata and documentation | Done | `task.toml` validated with Harbor 0.25.0 `TaskConfig`; `solution/TRUTH.md` complete |
| J. Peer review and submission | Blocked | Needs Thanatos project repo access, registry push procedure, and live judge/calibration |

## Environment & image evidence (verified 2026-11-25)

| Check | Result |
|---|---|
| `python --version` (host) | 3.13.17 (containers use Python 3.11.18) |
| `harbor --version` | `0.25.0`; `TaskConfig.model_validate_toml` valid (`schema_version = "1.4"`) |
| `than-httpbin-env:test` | `sha256:fc9de5f8bec72e3083eaa83f703319e63253f940ff953fb00ae42bf950bd8f40` |
| `than-httpbin-tests:test` | `sha256:d405fe6e7849fc32cdb87ba13bf4b389ccc0e88996345167f1c412d32ef7e97b` |
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
- **G7 (Judge Sanity):** BLOCKED (live API) — `judge.toml` validated (`openai/gpt-4.1`, 13 criteria); missing `OPENAI_API_KEY` test exited 1 with `verifier error: OPENAI_API_KEY is not set` / `scoring failed; no reward written` and wrote no reward file.

## Blockers & next steps

- Provide `OPENAI_API_KEY` and run live G7 judge check against the oracle and a failing trajectory.
- Run Phase H model trajectory calibration (`*opus*` + second model) to verify the `hard` difficulty band.
- Obtain Thanatos project repo URL, pre-push hooks, reference-commit fork push access, and image registry push procedure.
