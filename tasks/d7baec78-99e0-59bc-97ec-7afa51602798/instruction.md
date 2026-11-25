# Migrate httpbin from Flask to FastAPI

## Goal

Migrate the httpbin service in `/workspace` from **Flask 3.0.3 + Werkzeug 3.0.6, served by gunicorn 23.0.0**, to **FastAPI 0.115.6 + uvicorn 0.32.1**, so that no HTTP client of the service can tell the new artifact from the original. The migration must be complete in source, build metadata and the built artifact: the old stack may not survive under a new name, as a vendored copy, or as a dependency.

## Scope

The whole repository:

- every module under `httpbin/`: routing, request inspection, response construction, helpers, filters, templates, static assets, and the `httpbin/app.py` / `httpbin/core.py` / `httpbin/__init__.py` wiring;
- packaging and process files: `pyproject.toml`, `Dockerfile`, `Procfile`, `start.sh`, `httpbin.bash`, `runtime.txt`, `app.json`, `setup.cfg`, `tox.ini`, `MANIFEST.in`;
- the CI workflow under `.github/workflows/`, `README.md`, and the repository test suite under `tests/`.

The toolchain is Python 3.11. Build and test **offline** against the local wheel cache:

```bash
python -m pip install --no-index --find-links /opt/wheels/target --no-build-isolation '.[test]'
python -m pytest tests -q
```

`/opt/wheels/target` holds the target stack and its dependencies; `/opt/wheels/orig` holds the original stack and must not be used. `/opt/original/start.sh` starts the unmodified original on `127.0.0.1:8080`, so every behaviour below can be observed; stop it before starting your delivery, because both use that address.

Out of scope: adding routes or changing the URL map; adding an API-documentation UI (the original, as it runs here, serves the static landing page at `/` and has no `/spec.json`); persistent storage, metrics or auth backends beyond what the original implements; network access of any kind.

## Behaviour contract

The delivered artifact is installed with the offline command above and started as:

```bash
python -m uvicorn httpbin:app --host 127.0.0.1 --port 8080
```

Preserve, for every documented route, the status code, the headers and the body bytes. The graded surfaces are these twelve groups:

- **introspection** — `/`, `/html`, `/robots.txt`, `/json`, `/xml`, `/forms/post`, `/deny`, `/encoding/utf8`, `/links/*`, and the static assets the pages reference.
- **request_echo** — `/get`, `/headers`, `/user-agent`, `/ip`, `/anything` and `/anything/{path}`: query parsing (repeated keys, blank values, `+` versus `%2B`, percent-encoding), echoed headers including repeats, and the `Host`, `Origin`, `X-Forwarded-For`, `X-Forwarded-Proto` and `X-Forwarded-Host` echoes.
- **payload_echo** — `/post`, `/put`, `/patch`, `/delete`, `/anything`: form-urlencoded, raw, binary, JSON (objects, scalars, invalid JSON, unicode), multipart with and without a file content type, chunked bodies, and empty bodies.
- **status_codes** — `/status/<codes>` for single codes, weighted choices, invalid codes and comma-separated codes, plus HEAD variants.
- **redirects** — `/redirect/*`, `/relative-redirect/*`, `/absolute-redirect/*`, `/redirect-to` with each allowed status and invalid input, and the `Location` values they produce.
- **cookies** — `/cookies`, `/cookies/set`, `/cookies/set/{name}/{value}`, `/cookies/delete`: reading, quoting, `show_env`, setting and deleting, including `Set-Cookie` attributes and ordering.
- **auth** — `/basic-auth/*`, `/hidden-basic-auth/*`, `/bearer`, `/digest-auth/*` with MD5 and SHA-256, `auth` and `auth-int`, correct and wrong passwords, and the 401 challenge.
- **compression** — `/gzip`, `/deflate`, `/brotli` and `Accept-Encoding` negotiation, including when the client asks for something else.
- **content_negotiation** — `/image`, `/image/png`, `/image/jpeg`, `/image/webp`, `/image/svg`, `Accept` handling, 406 and HEAD.
- **streaming_ranges** — `/bytes`, `/stream`, `/stream-bytes`, `/range` (full, single, suffix, open-ended, unsatisfiable and malformed ranges), `/drip` and `/delay`.
- **response_headers_cache** — `/response-headers`, `/cache`, `/cache/{seconds}`, `/etag/{etag}` with `If-None-Match` and `If-Match`, including weak tags, lists and `*`.
- **protocol_errors_cors** — 404 and 405 bodies, `Allow`, OPTIONS/HEAD/TRACE, trailing slashes, CORS response headers and preflight, and malformed inputs such as a bad delay or bad base64.

These must match exactly:

- the status code for every request, including error and edge cases;
- header names, values, repeats and order where observable, including `Set-Cookie`, `Allow`, `Access-Control-*`, `WWW-Authenticate`, `Content-Type` and `Content-Encoding`;
- the response body byte-for-byte: JSON key order, indentation and trailing newline, rendered HTML, and binary payloads;
- the behaviour asserted by the repository's own test suite, which must still pass.

These need not match, because the original does not reproduce them between runs or they are transport-level:

- `Date`, `Server`, `Connection`, `Transfer-Encoding`, `Keep-Alive`, and `Content-Length` — except on HEAD responses, where `Content-Length` is compared;
- the order of tokens in `Allow`;
- the digest `nonce`, `opaque` and `stale` values;
- the `stale_after`, `last_nonce` and `expires` values in `Set-Cookie`;
- the `Last-Modified` header and the random 32-hex `ETag` that `/cache` generates;
- the value returned by `/uuid`;
- loopback host and port inside echoed URLs, `Host` headers and `origin`;
- documentation prose that names the server or the interface: the new stack's names may replace the old ones;
- compressed bodies are compared after the HTTP client decompresses them; the `Content-Encoding` header is still compared.

Two repository tests need care. The landing-page test whose name contains a forbidden word must be replaced by a test named `test_index_serves_legacy_landing_page` that asserts the static landing page is served without the API-docs UI. The chunked-POST test keeps its name: the old test client could not send a chunked body and asserted 501, but the live original already accepts chunked requests, so under ASGI the test must assert that the body is accepted and echoed. All other original test names must be kept.

## Constraints and red lines

Work offline against `/opt/wheels/target`. Do not read, modify or execute anything under `/tests`, `/opt/original`, `/opt/orig-venv` or `/opt/requirements-original.txt`, and do not try to discover or weaken the hidden tests.

Each of the following is a **red line**: if the verifier finds one, the score is 0 before the judge runs.

- `pyproject.toml` is missing, there is no Python source, no source file imports `fastapi` or `starlette`, or the build metadata still declares the old stack.
- Any delivered file, by path or by text, contains one of the whole words (case-insensitive) `flask`, `werkzeug`, `flasgger`, `gunicorn`, `gevent`; or `httpbin/templates/flasgger/` still exists.
- Any delivered text file references `/opt/original`, `/opt/orig-venv`, `/opt/requirements-original` or `/opt/wheels/orig`, i.e. a wrapper, shim, exec or link to the original implementation.
- `pyproject.toml` does not pin `fastapi==0.115.6` or does not declare `uvicorn`.
- The offline build fails, or produces no wheel.
- The built wheel or the installed metadata contains, ships or requires a forbidden package, does not require `fastapi`, or ships an unexpected top-level package.
- `tests/test_httpbin.py` is missing, no longer defines `HttpbinTestCase`, removes an original test (other than the one replacement above), or stops using a test runner.
- The repository test suite fails or skips tests.
- The delivery wraps, vendors, embeds, executes or proxies the original implementation in any other way.

## Scoring

The behaviour score is the share of the **12 behaviour groups** that pass. Each group passes only if every probe in it matches the live original; groups have equal weight. The judge score is the share of the **13 binary criteria** (6 instruction-following, 4 code-quality, 3 process) answered `yes`.

```text
reward = 0.8 * behaviour_score + 0.2 * judge_score
```

An absent migration or any red line forces `reward = 0.0` and skips the judge. A failure of the grading infrastructure — a missing toolchain, an unreadable reference artifact, a suite that cannot run, or judge evidence over 1 MiB — is a verifier error: no reward is written.
