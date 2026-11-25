#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hidden differential verifier for the httpbin Flask -> FastAPI migration.

The verifier never stores expected answers.  It starts the pristine original,
records its answers, starts the delivered package, records its answers, and
compares the two after folding only the values the original itself does not
reproduce between runs.

Subcommands
-----------
--absent-gate       exit 0 when a migration is present, 3 when it is absent
--measure           build, run, compare; write a verdict JSON (--out)
--judge-evidence    write changes.diff and trajectory.json (--out-dir)
--score             turn a verdict into reward.json (--out)
--list-groups       print the probe registry as JSON (build-time helper)
--selfcheck         validate config.json against the probe registry
"""

import argparse
import ast
import base64
import codecs
import difflib
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
JUDGE_PATH = HERE / "judge.toml"

WORKSPACE = Path(os.environ.get("THAN_WORKSPACE", "/workspace"))
ORIGINAL = Path("/opt/original")
ORIG_START = ORIGINAL / "start.sh"
WHEELS = Path("/opt/wheels/target")
PORT = 8080
BASE = "http://127.0.0.1:%d" % PORT
SANDBOX_UID = 1000
SANDBOX_GID = 1000
PROBE_UA = "than-probe/1"
FORBIDDEN = ("flask", "werkzeug", "flasgger", "gunicorn", "gevent")
FORBIDDEN_RE = re.compile(r"(?i)\b(" + "|".join(FORBIDDEN) + r")\b")
# References to the reference implementation or its private virtualenv are a
# wrapper/shim/exec red line, whether or not the old stack is named.
REFERENCE_PATHS = (
    "/opt/original",
    "/opt/orig-venv",
    "/opt/requirements-original",
    "/opt/wheels/orig",
)
REFERENCE_MARKERS = REFERENCE_PATHS + (
    "orig-venv",
    "requirements-original",
    "wheels/orig",
    "start-original.sh",
)
MAX_JUDGE_DIFF = 1024 * 1024

TEXT_EXTS = {
    ".py", ".pyi", ".toml", ".txt", ".md", ".rst", ".cfg", ".ini", ".sh",
    ".bash", ".yml", ".yaml", ".json", ".html", ".htm", ".in", ".js", ".css",
    ".dockerfile", ".gitignore", ".dockerignore", ".editorconfig",
}
TEXT_NAMES = {"Dockerfile", "Procfile", "Makefile", "MANIFEST.in", "AUTHORS", "LICENSE"}

SKIP_DIRS = {
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tox", "node_modules", ".venv", "venv", "env", "dist", "build",
    ".eggs",
}
SKIP_SUFFIXES = (".pyc", ".pyo", ".egg-info", ".whl", ".tar.gz", ".zip")

# Directories excluded from the judge diff (build output, VCS, IDE state).
DIFF_SKIP_DIRS = SKIP_DIRS | {".idea", ".vscode", ".than-venv", ".than-dist"}


class VerifierError(RuntimeError):
    """An infrastructure fault: exit non-zero and write no reward."""


# ---------------------------------------------------------------------------
# Probe registry
# ---------------------------------------------------------------------------

GROUPS = {
    "introspection": "Landing page, static documents, links and repository-served assets.",
    "request_echo": "Request line, query string, headers, host and forwarding echoes.",
    "payload_echo": "Form, raw, JSON, multipart and chunked request bodies.",
    "status_codes": "Every status endpoint, including multi-code and invalid codes.",
    "redirects": "Relative, absolute and redirect-to responses and Location values.",
    "cookies": "Cookie reading, setting, deleting and re-echoing.",
    "auth": "Basic, hidden-basic, bearer and digest authentication.",
    "compression": "gzip, deflate and brotli response encoding.",
    "content_negotiation": "Image content negotiation and unacceptable media types.",
    "streaming_ranges": "Random bytes, streams, drip/delay and HTTP range responses.",
    "response_headers_cache": "response-headers, cache, cache-control and etag semantics.",
    "protocol_errors_cors": "404/405/Allow, OPTIONS/HEAD/TRACE, CORS and malformed inputs.",
}


def _h(pairs):
    return [tuple(p) for p in (pairs or [])]


def probe_registry():
    probes = []

    def add(pid, group, method, path, headers=None, body=None, kind="normal", **extra):
        item = {
            "id": pid,
            "group": group,
            "method": method,
            "path": path,
            "headers": _h(headers),
            "body": body,
            "kind": kind,
        }
        item.update(extra)
        probes.append(item)

    # -- introspection ------------------------------------------------------
    add("landing", "introspection", "GET", "/")
    add("html", "introspection", "GET", "/html")
    add("robots", "introspection", "GET", "/robots.txt")
    add("json_doc", "introspection", "GET", "/json")
    add("xml_doc", "introspection", "GET", "/xml")
    add("forms_page", "introspection", "GET", "/forms/post")
    add("deny", "introspection", "GET", "/deny")
    add("utf8", "introspection", "GET", "/encoding/utf8")
    add("links_3_1", "introspection", "GET", "/links/3/1")
    add("links_2_3", "introspection", "GET", "/links/2/3")
    add("links_2", "introspection", "GET", "/links/2")
    add("links_1", "introspection", "GET", "/links/1")

    # -- request_echo -------------------------------------------------------
    add("get_basic", "request_echo", "GET", "/get?alpha=1&beta=two")
    add("get_multivalue", "request_echo", "GET",
        "/get?k=1&k=2&blank=&sp=a+b&pct=%E2%9C%93")
    add("get_plus", "request_echo", "GET", "/get?q=a+b&r=a%2Bb")
    add("get_semicolon", "request_echo", "GET", "/get?a=1;b=2")
    add("get_unicode", "request_echo", "GET", "/get?q=%C3%A9")
    add("get_reserved", "request_echo", "GET", "/get?x=%2F&y=%2B&z=%23&w=%20")
    add("headers_custom", "request_echo", "GET", "/headers",
        [("X-Custom", "hello")])
    add("headers_multi", "request_echo", "GET", "/headers",
        [("X-Multi", "a"), ("X-Multi", "b")])
    add("user_agent", "request_echo", "GET", "/user-agent")
    add("ip", "request_echo", "GET", "/ip")
    add("anything_path", "request_echo", "GET", "/anything/a/b/c?q=1")
    add("anything_root", "request_echo", "GET", "/anything")
    add("anything_query", "request_echo", "GET", "/anything?x=1&x=2")
    add("xforwarded", "request_echo", "GET", "/get",
        [("X-Forwarded-For", "10.0.0.9"),
         ("X-Forwarded-Proto", "https"),
         ("X-Forwarded-Host", "upstream.test")])
    add("host_header", "request_echo", "GET", "/get",
        [("Host", "example.test:1234")])
    add("origin_echo", "request_echo", "GET", "/get",
        [("Origin", "http://x.test")])
    add("accept_echo", "request_echo", "GET", "/get",
        [("Accept", "application/json")])

    # -- payload_echo -------------------------------------------------------
    add("post_form", "payload_echo", "POST", "/post",
        [("Content-Type", "application/x-www-form-urlencoded")],
        b"a=1&b=two&b=three")
    add("post_form_special", "payload_echo", "POST", "/post",
        [("Content-Type", "application/x-www-form-urlencoded")],
        b"a=%C3%A9&b=x+y")
    add("post_raw", "payload_echo", "POST", "/post",
        [("Content-Type", "text/plain")], b"raw-body-\xff")
    add("post_binary", "payload_echo", "POST", "/post",
        [("Content-Type", "application/octet-stream")], b"\x01\x02\x03\x81\x82\x83")
    add("post_json", "payload_echo", "POST", "/post",
        [("Content-Type", "application/json")], b'{"n": 1, "s": "\xc3\xa9"}')
    add("post_json_unicode", "payload_echo", "POST", "/post",
        [("Content-Type", "application/json")],
        '{"k": "v\u00e9", "n": [1, 2.5, null, true]}'.encode("utf-8"))
    add("post_json_number", "payload_echo", "POST", "/post",
        [("Content-Type", "application/json")], b"42")
    add("post_json_string", "payload_echo", "POST", "/post",
        [("Content-Type", "application/json")], b'"hi"')
    add("post_json_invalid", "payload_echo", "POST", "/post",
        [("Content-Type", "application/json")], b"{bad")
    add("post_json_charset", "payload_echo", "POST", "/anything",
        [("Content-Type", "application/json; charset=utf-8")], b'{"a":1}')
    add("put_form", "payload_echo", "PUT", "/put",
        [("Content-Type", "application/x-www-form-urlencoded")], b"x=9")
    add("patch_json", "payload_echo", "PATCH", "/patch",
        [("Content-Type", "application/json")], b'{"p": 1}')
    add("delete", "payload_echo", "DELETE", "/delete")
    add("anything_post", "payload_echo", "POST", "/anything",
        [("Content-Type", "application/json")], b'{"n": 1}')
    add("multipart_file", "payload_echo", "POST", "/post",
        [("Content-Type", "multipart/form-data; boundary=X")],
        b"--X\r\nContent-Disposition: form-data; name=\"f\"; filename=\"a.txt\"\r\n"
        b"Content-Type: text/plain\r\n\r\nhello\r\n--X--\r\n")
    add("multipart_field", "payload_echo", "POST", "/post",
        [("Content-Type", "multipart/form-data; boundary=X")],
        b"--X\r\nContent-Disposition: form-data; name=\"k\"\r\n\r\nv\r\n--X--\r\n")
    add("multipart_missing_ct", "payload_echo", "POST", "/post",
        [("Content-Type", "multipart/form-data; boundary=X")],
        b"--X\r\nContent-Disposition: form-data; name=\"f\"; filename=\"a.txt\"\r\n\r\n"
        b"hello\r\n--X--\r\n")
    add("chunked_json", "payload_echo", "POST", "/post",
        [("Content-Type", "application/json")], kind="chunked",
        chunks=[b'{"animal":', b'"dog"}'])
    add("post_empty", "payload_echo", "POST", "/post")
    add("post_unicode_form", "payload_echo", "POST", "/post",
        [("Content-Type", "application/x-www-form-urlencoded")],
        b"name=%E2%9C%93")

    # -- status_codes -------------------------------------------------------
    for code in (200, 201, 204, 301, 302, 304, 400, 401, 402, 403, 404,
                 405, 406, 418, 500, 599):
        add("status_%d" % code, "status_codes", "GET", "/status/%d" % code)
    add("status_weighted", "status_codes", "GET", "/status/200:0,402:0,418:1")
    add("status_bad_weight", "status_codes", "GET", "/status/200:x")
    add("status_invalid", "status_codes", "GET", "/status/abc")
    add("status_invalid_combo", "status_codes", "GET", "/status/4!9")
    add("head_status_418", "status_codes", "HEAD", "/status/418")
    add("head_status_204", "status_codes", "HEAD", "/status/204")

    # -- redirects ----------------------------------------------------------
    add("redirect_1", "redirects", "GET", "/redirect/1")
    add("redirect_2", "redirects", "GET", "/redirect/2")
    add("redirect_3", "redirects", "GET", "/redirect/3")
    add("redirect_2_abs", "redirects", "GET", "/redirect/2?absolute=true")
    add("relative_redirect_1", "redirects", "GET", "/relative-redirect/1")
    add("relative_redirect_2", "redirects", "GET", "/relative-redirect/2")
    add("absolute_redirect_1", "redirects", "GET", "/absolute-redirect/1")
    add("absolute_redirect_2", "redirects", "GET", "/absolute-redirect/2")
    add("redirect_to_301", "redirects", "GET",
        "/redirect-to?url=https%3A%2F%2Fexample.com%2Fa&status_code=301")
    add("redirect_to_default", "redirects", "GET", "/redirect-to?url=%2Fget")
    add("redirect_to_missing", "redirects", "GET", "/redirect-to")
    add("redirect_to_200", "redirects", "GET",
        "/redirect-to?url=/get&status_code=200")
    add("redirect_to_308_post", "redirects", "POST",
        "/redirect-to?url=/get&status_code=308",
        [("Content-Type", "application/x-www-form-urlencoded")], b"a=1")
    add("redirect_to_307_post", "redirects", "POST",
        "/redirect-to?url=/post&status_code=307",
        [("Content-Type", "application/octet-stream")],
        b"\x01\x02\x03\x81\x82\x83")

    # -- cookies ------------------------------------------------------------
    add("cookies_read", "cookies", "GET", "/cookies",
        [("Cookie", "a=1; b=two")])
    add("cookies_read_env", "cookies", "GET", "/cookies?show_env=1",
        [("Cookie", "__utma=x; a=1")])
    add("cookies_set_query", "cookies", "GET", "/cookies/set?a=1&b=2")
    add("cookies_set_path", "cookies", "GET", "/cookies/set/k/v")
    add("cookies_special", "cookies", "GET", "/cookies/set?v=a%20b%3Bc")
    add("cookies_delete", "cookies", "GET", "/cookies/delete?a=1",
        [("Cookie", "a=1")])
    add("cookies_echo_quoted", "cookies", "GET", "/cookies",
        [("Cookie", 'q="a b"; plain=x')])
    add("cookies_multi", "cookies", "GET", "/cookies",
        [("Cookie", "a=1; b=2; c=3")])
    add("cookies_empty", "cookies", "GET", "/cookies")

    # -- auth ---------------------------------------------------------------
    add("basic_missing", "auth", "GET", "/basic-auth/user/passwd")
    add("basic_wrong", "auth", "GET", "/basic-auth/user/passwd",
        [("Authorization", "Basic dXNlcjpiYWQ=")])
    add("basic_ok", "auth", "GET", "/basic-auth/user/passwd",
        [("Authorization", "Basic dXNlcjpwYXNzd2Q=")])
    add("hidden_missing", "auth", "GET", "/hidden-basic-auth/user/passwd")
    add("hidden_ok", "auth", "GET", "/hidden-basic-auth/user/passwd",
        [("Authorization", "Basic dXNlcjpwYXNzd2Q=")])
    add("bearer_missing", "auth", "GET", "/bearer")
    add("bearer_ok", "auth", "GET", "/bearer",
        [("Authorization", "Bearer tok-123")])
    add("bearer_wrong_type", "auth", "GET", "/bearer",
        [("Authorization", "Basic dXNlcjpwYXNzd2Q=")])
    add("digest_challenge", "auth", "GET", "/digest-auth/auth/user/passwd")
    add("digest_md5", "auth", "GET", "/digest-auth/auth/user/passwd/MD5",
        kind="digest", qop="auth", user="user", passwd="passwd",
        algorithm="MD5", bad_password=False)
    add("digest_sha256", "auth", "GET", "/digest-auth/auth/user/passwd/SHA-256",
        kind="digest", qop="auth", user="user", passwd="passwd",
        algorithm="SHA-256", bad_password=False)
    add("digest_auth_int", "auth", "GET", "/digest-auth/auth-int/user/passwd/MD5",
        kind="digest", qop="auth-int", user="user", passwd="passwd",
        algorithm="MD5", bad_password=False)
    add("digest_wrong_password", "auth", "GET",
        "/digest-auth/auth/user/passwd/MD5", kind="digest", qop="auth",
        user="user", passwd="passwd", algorithm="MD5", bad_password=True)

    # -- compression --------------------------------------------------------
    add("gzip", "compression", "GET", "/gzip")
    add("deflate", "compression", "GET", "/deflate")
    add("brotli", "compression", "GET", "/brotli")
    add("gzip_identity", "compression", "GET", "/gzip",
        [("Accept-Encoding", "identity")])
    add("deflate_gzip", "compression", "GET", "/deflate",
        [("Accept-Encoding", "gzip")])
    add("brotli_gzip", "compression", "GET", "/brotli",
        [("Accept-Encoding", "gzip")])
    add("get_accept_encoding", "compression", "GET", "/get",
        [("Accept-Encoding", "gzip, deflate, br")])
    add("anything_accept_encoding", "compression", "GET", "/anything",
        [("Accept-Encoding", "identity")])

    # -- content_negotiation ------------------------------------------------
    add("image_default", "content_negotiation", "GET", "/image")
    add("image_png", "content_negotiation", "GET", "/image/png")
    add("image_jpeg", "content_negotiation", "GET", "/image/jpeg")
    add("image_webp", "content_negotiation", "GET", "/image/webp")
    add("image_svg", "content_negotiation", "GET", "/image/svg")
    add("image_accept_svg", "content_negotiation", "GET", "/image",
        [("Accept", "image/svg+xml")])
    add("image_accept_png_q", "content_negotiation", "GET", "/image",
        [("Accept", "image/png,image/*;q=0.8")])
    add("image_accept_star", "content_negotiation", "GET", "/image",
        [("Accept", "*/*")])
    add("image_not_acceptable", "content_negotiation", "GET", "/image",
        [("Accept", "text/plain")])
    add("image_bad_format", "content_negotiation", "GET", "/image/gif")
    add("image_head", "content_negotiation", "HEAD", "/image/png")

    # -- streaming_ranges ---------------------------------------------------
    add("bytes_seed", "streaming_ranges", "GET", "/bytes/8?seed=42")
    add("bytes_zero", "streaming_ranges", "GET", "/bytes/0")
    add("bytes_large", "streaming_ranges", "GET", "/bytes/1024?seed=1")
    add("bytes_negative", "streaming_ranges", "GET", "/bytes/-1")
    add("stream_2", "streaming_ranges", "GET", "/stream/2")
    add("stream_5", "streaming_ranges", "GET", "/stream/5")
    add("stream_bytes_seed", "streaming_ranges", "GET",
        "/stream-bytes/8?seed=7&chunk_size=3")
    add("stream_bytes_chunk", "streaming_ranges", "GET",
        "/stream-bytes/16?seed=3&chunk_size=5")
    add("range_full", "streaming_ranges", "GET", "/range/8")
    add("range_partial", "streaming_ranges", "GET", "/range/8",
        [("Range", "bytes=2-5")])
    add("range_suffix", "streaming_ranges", "GET", "/range/8",
        [("Range", "bytes=-3")])
    add("range_open", "streaming_ranges", "GET", "/range/8",
        [("Range", "bytes=5-")])
    add("range_unsatisfiable", "streaming_ranges", "GET", "/range/8",
        [("Range", "bytes=20-30")])
    add("range_out_of_bounds", "streaming_ranges", "GET", "/range/8",
        [("Range", "bytes=10-5")])
    add("range_zero", "streaming_ranges", "GET", "/range/0")
    add("range_chunk", "streaming_ranges", "GET", "/range/8?chunk_size=2")
    add("range_bad_header", "streaming_ranges", "GET", "/range/8",
        [("Range", "bytes=abc")])
    add("drip_3", "streaming_ranges", "GET",
        "/drip?duration=0&numbytes=3&delay=0")
    add("drip_zero", "streaming_ranges", "GET",
        "/drip?duration=0&numbytes=0&delay=0")
    add("drip_invalid", "streaming_ranges", "GET",
        "/drip?duration=0&numbytes=-1&delay=0")
    add("delay_small", "streaming_ranges", "GET", "/delay/0.01")

    # -- response_headers_cache --------------------------------------------
    add("rh_simple", "response_headers_cache", "GET",
        "/response-headers?animal=dog")
    add("rh_multi", "response_headers_cache", "GET",
        "/response-headers?X-D=1&X-D=2")
    add("rh_overwrite_ct", "response_headers_cache", "GET",
        "/response-headers?Content-Type=text/plain")
    add("rh_post", "response_headers_cache", "POST",
        "/response-headers?X-Test=1",
        [("Content-Type", "application/x-www-form-urlencoded")], b"ignored=1")
    add("cache_plain", "response_headers_cache", "GET", "/cache")
    add("cache_inm", "response_headers_cache", "GET", "/cache",
        [("If-None-Match", "anything")])
    add("cache_ims", "response_headers_cache", "GET", "/cache",
        [("If-Modified-Since", "Wed, 21 Oct 2015 07:28:00 GMT")])
    add("cache_control_60", "response_headers_cache", "GET", "/cache/60")
    add("cache_control_0", "response_headers_cache", "GET", "/cache/0")
    add("etag_plain", "response_headers_cache", "GET", "/etag/abc")
    add("etag_inm_match", "response_headers_cache", "GET", "/etag/abc",
        [("If-None-Match", '"abc"')])
    add("etag_inm_star", "response_headers_cache", "GET", "/etag/abc",
        [("If-None-Match", "*")])
    add("etag_inm_list", "response_headers_cache", "GET", "/etag/abc",
        [("If-None-Match", '"x", "abc"')])
    add("etag_inm_weak", "response_headers_cache", "GET", "/etag/abc",
        [("If-None-Match", 'W/"abc"')])
    add("etag_ifmatch_mismatch", "response_headers_cache", "GET", "/etag/abc",
        [("If-Match", '"zzz"')])
    add("etag_ifmatch_match", "response_headers_cache", "GET", "/etag/abc",
        [("If-Match", '"abc"')])
    add("etag_ifmatch_star", "response_headers_cache", "GET", "/etag/abc",
        [("If-Match", "*")])

    # -- protocol_errors_cors ----------------------------------------------
    add("nf_get", "protocol_errors_cors", "GET", "/nope")
    add("nf_post", "protocol_errors_cors", "POST", "/nope", body=b"x")
    add("nf_head", "protocol_errors_cors", "HEAD", "/nope")
    add("nf_options", "protocol_errors_cors", "OPTIONS", "/nope")
    add("mna_post_get", "protocol_errors_cors", "POST", "/get", body=b"x=1",
        headers=[("Content-Type", "application/x-www-form-urlencoded")])
    add("allow_put_get", "protocol_errors_cors", "PUT", "/get", body=b"")
    add("allow_patch_post", "protocol_errors_cors", "PATCH", "/post", body=b"")
    add("options_get", "protocol_errors_cors", "OPTIONS", "/get")
    add("head_get", "protocol_errors_cors", "HEAD", "/get")
    add("trace_anything", "protocol_errors_cors", "TRACE", "/anything")
    add("trailing_slash", "protocol_errors_cors", "GET", "/get/")
    add("cors_preflight", "protocol_errors_cors", "OPTIONS", "/get",
        [("Origin", "http://x.test"),
         ("Access-Control-Request-Headers", "X-A, X-B")])
    add("cors_preflight_put", "protocol_errors_cors", "OPTIONS", "/put",
        [("Origin", "http://x.test")])
    add("cors_origin", "protocol_errors_cors", "GET", "/get",
        [("Origin", "http://x.test")])
    add("cors_credentials", "protocol_errors_cors", "GET",
        "/basic-auth/user/passwd",
        [("Origin", "http://x.test"),
         ("Authorization", "Basic dXNlcjpwYXNzd2Q=")])
    add("cors_error", "protocol_errors_cors", "GET", "/delay/abc",
        [("Origin", "http://o")])
    add("bad_delay", "protocol_errors_cors", "GET", "/delay/abc")
    add("bad_base64", "protocol_errors_cors", "GET", "/base64/!!!")
    add("base64_ok", "protocol_errors_cors", "GET",
        "/base64/SFRUUEJJTiBpcyBhd2Vzb21l")
    add("base64_pad", "protocol_errors_cors", "GET",
        "/base64/SFRUUEJJTiBpcyBhd2Vzb21l=")
    add("base64_empty", "protocol_errors_cors", "GET", "/base64/")

    return probes


# ---------------------------------------------------------------------------
# HTTP recording and normalisation
# ---------------------------------------------------------------------------


class Chunks(object):
    def __init__(self, parts):
        self.parts = list(parts)

    def __iter__(self):
        for part in self.parts:
            yield part


def _snapshot(response):
    return {
        "status": response.status_code,
        "headers": [[str(k).lower(), str(v)]
                    for k, v in response.headers.multi_items()],
        "body": base64.b64encode(response.content).decode("ascii"),
    }


def _parse_challenge(value):
    if not value:
        return {}
    value = value.strip()
    if value.lower().startswith("digest "):
        value = value[7:]
    result = {}
    for match in re.finditer(r'([A-Za-z0-9_-]+)\s*=\s*("([^"]*)"|[^,\s]+)', value):
        result[match.group(1).lower()] = match.group(3) if match.group(3) is not None else match.group(2)
    return result


def _hash(data, algorithm):
    if algorithm == "SHA-256":
        return hashlib.sha256(data).hexdigest()
    if algorithm == "SHA-512":
        return hashlib.sha512(data).hexdigest()
    return hashlib.md5(data).hexdigest()


def _digest_header(username, password, method, uri, challenge, qop,
                   algorithm, body=b"", bad_password=False):
    nonce = challenge.get("nonce", "")
    realm = challenge.get("realm", "")
    opaque = challenge.get("opaque", "")
    password = password + ("-wrong" if bad_password else "")
    ha1 = _hash(("%s:%s:%s" % (username, realm, password)).encode("utf-8"), algorithm)
    a2 = "%s:%s" % (method, uri)
    if qop == "auth-int":
        a2 = "%s:%s" % (a2, _hash(body, algorithm))
    ha2 = _hash(a2.encode("utf-8"), algorithm)
    cnonce = "0a4f113b"
    nc = "00000001"
    if qop in ("auth", "auth-int"):
        a3 = "%s:%s:%s:%s:%s:%s" % (ha1, nonce, nc, cnonce, qop, ha2)
    else:
        a3 = "%s:%s:%s" % (ha1, nonce, ha2)
    response = _hash(a3.encode("utf-8"), algorithm)
    parts = [
        'username="%s"' % username,
        'response="%s"' % response,
        'uri="%s"' % uri,
        'nonce="%s"' % nonce,
        'realm="%s"' % realm,
        'opaque="%s"' % opaque,
        'algorithm="%s"' % algorithm,
    ]
    if qop in ("auth", "auth-int"):
        parts += ['cnonce="%s"' % cnonce, "nc=%s" % nc, "qop=%s" % qop]
    return "Digest " + ", ".join(parts)


def _run_digest(client, probe):
    qop = probe["qop"]
    user = probe["user"]
    passwd = probe["passwd"]
    algorithm = probe["algorithm"]
    path = probe["path"]
    first = client.get(path)
    challenge_rec = _snapshot(first)
    challenge = _parse_challenge(first.headers.get("www-authenticate"))
    if first.status_code != 401 or not challenge:
        return {"parts": {"challenge": challenge_rec, "response": challenge_rec}}
    header = _digest_header(user, passwd, "GET", path, challenge, qop,
                            algorithm, bad_password=probe.get("bad_password", False))
    second = client.get(path, headers={"Authorization": header})
    return {"parts": {"challenge": challenge_rec, "response": _snapshot(second)}}


def run_probe(base, probe):
    import httpx

    with httpx.Client(base_url=base, follow_redirects=False, timeout=30.0,
                      trust_env=False, headers={"User-Agent": PROBE_UA}) as client:
        if probe["kind"] == "digest":
            return _run_digest(client, probe)
        if probe["kind"] == "chunked":
            content = Chunks(probe.get("chunks") or [])
            headers = list(probe["headers"])
            if not any(k.lower() == "transfer-encoding" for k, _ in headers):
                headers.append(("Transfer-Encoding", "chunked"))
        else:
            content = probe.get("body")
            headers = list(probe["headers"]) or None
        response = client.request(probe["method"], probe["path"],
                                  content=content, headers=headers)
        return _snapshot(response)


def record_all(base, probes, tolerate=False):
    records = {}
    for probe in probes:
        try:
            records[probe["id"]] = run_probe(base, probe)
        except Exception as exc:  # noqa: BLE001 - reported as a probe failure
            if not tolerate:
                raise VerifierError("the original failed probe %s: %s: %s"
                                    % (probe["id"], type(exc).__name__, exc))
            records[probe["id"]] = {"error": "%s: %s" % (type(exc).__name__, exc)}
    return records


# --- folds ----------------------------------------------------------------

_HOST_RE = re.compile(
    rb"(?:(?:127\.0\.0\.1|172\.17\.0\.1|localhost):(?:8080|18080|18081))"
)
_LOOPBACK_RE = re.compile(rb"(?:127\.0\.0\.1|172\.17\.0\.1)")
_UUID_RE = re.compile(
    rb"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_HEX32_RE = re.compile(rb"(?<![0-9a-fA-F])[0-9a-fA-F]{32}(?![0-9a-fA-F])")

DROP_HEADERS = {"date", "server", "connection", "transfer-encoding",
                "keep-alive", "content-length"}


def _fold_text(value):
    value = _HOST_RE.sub(b"{host}", value)
    value = _LOOPBACK_RE.sub(b"{loopback}", value)
    value = _UUID_RE.sub(b"{uuid}", value)
    # Documentation prose may name the new stack where the old one was named.
    value = value.replace(b"Gunicorn", b"Uvicorn").replace(b"gunicorn", b"uvicorn")
    value = value.replace(b"WSGI", b"ASGI")
    value = value.replace(b"a ASGI", b"an ASGI").replace(b"A ASGI", b"An ASGI")
    return value


def _norm_body(body):
    body = _fold_text(body)
    body = _HEX32_RE.sub(b"{hex32}", body)
    return body


def _norm_header_value(name, value, method):
    raw = _fold_text(value.encode("latin-1", "replace"))
    if name == "allow":
        tokens = sorted(t.strip().lower() for t in raw.decode("latin-1").split(",") if t.strip())
        return ",".join(tokens)
    if name == "www-authenticate":
        text = raw.decode("latin-1")
        text = re.sub(r'(?i)nonce="[^"]*"', 'nonce="{nonce}"', text)
        text = re.sub(r'(?i)opaque="[^"]*"', 'opaque="{opaque}"', text)
        text = re.sub(r"(?i)\bstale=(?:true|false)", "stale={stale}", text)
        return text
    if name == "set-cookie":
        text = raw.decode("latin-1")
        text = re.sub(r"(?i)stale_after=[^;]*", "stale_after={stale_after}", text)
        text = re.sub(r"(?i)last_nonce=[^;]*", "last_nonce={last_nonce}", text)
        text = re.sub(r"(?i)expires=[^;]*", "expires={expires}", text)
        return text
    if name == "etag":
        text = raw.decode("latin-1")
        if re.fullmatch(r'"?[0-9a-fA-F]{32}"?', text):
            return "{random-etag}"
        return text
    if name == "last-modified":
        return "{http-date}"
    if name in ("location", "content-location"):
        return raw.decode("latin-1")
    return raw.decode("latin-1")


def norm_headers(pairs, method):
    grouped = {}
    for name, value in pairs:
        name = name.lower()
        if name in DROP_HEADERS:
            if not (name == "content-length" and method == "HEAD"):
                continue
        grouped.setdefault(name, []).append(_norm_header_value(name, value, method))
    return {name: grouped[name] for name in sorted(grouped)}


def norm_record(record, probe):
    if "parts" in record:
        return {"parts": {key: norm_record(value, probe)
                          for key, value in record["parts"].items()}}
    body = base64.b64decode(record["body"])
    return {
        "status": record["status"],
        "headers": norm_headers(record["headers"], probe["method"]),
        "body": _norm_body(body),
    }


def diff_records(original, delivered, probe):
    """Return a list of human-readable differences (empty means equal)."""
    if "error" in original or "error" in delivered:
        return ["probe error: original=%r delivered=%r"
                % (original.get("error"), delivered.get("error"))]
    left = norm_record(original, probe)
    right = norm_record(delivered, probe)
    if left == right:
        return []
    if "parts" in left or "parts" in right:
        problems = []
        for key in sorted(set(left.get("parts", {})) | set(right.get("parts", {}))):
            sub_probe = dict(probe)
            problems.extend(
                "%s: %s" % (key, p)
                for p in diff_records(
                    original["parts"][key], delivered["parts"][key], sub_probe
                )
            )
        return problems
    problems = []
    if left["status"] != right["status"]:
        problems.append("status %r != %r" % (left["status"], right["status"]))
    if left["headers"] != right["headers"]:
        names = sorted(set(left["headers"]) | set(right["headers"]))
        for name in names:
            if left["headers"].get(name) != right["headers"].get(name):
                problems.append("header %s: %r != %r" % (
                    name, left["headers"].get(name), right["headers"].get(name)))
    if left["body"] != right["body"]:
        problems.append("body: %s (%d bytes) != %s (%d bytes)" % (
            hashlib.sha256(left["body"]).hexdigest()[:16], len(left["body"]),
            hashlib.sha256(right["body"]).hexdigest()[:16], len(right["body"])))
    return problems


# ---------------------------------------------------------------------------
# Static gates
# ---------------------------------------------------------------------------


def iter_source_files(root):
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIRS and not d.endswith(".egg-info")
        )
        for filename in sorted(filenames):
            path = Path(dirpath) / filename
            if filename.endswith(SKIP_SUFFIXES):
                continue
            yield path


def is_text_file(path):
    path = Path(path)
    if path.name in TEXT_NAMES:
        return True
    return path.suffix.lower() in TEXT_EXTS


def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


def normalize_dep(value):
    return re.sub(r"\s+", "", value).lower()


def _is_printable_text(data):
    if not data:
        return False
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return all(ch in "\t\r\n" or 32 <= ord(ch) <= 126 for ch in text)


def _match_reference(text):
    for marker in REFERENCE_MARKERS:
        if marker in text:
            return marker
    return None


def _check_symlinks(workspace):
    hits = []
    ws_real = os.path.realpath(str(workspace))
    for dirpath, dirnames, filenames in os.walk(workspace, followlinks=False):
        kept = []
        for d in sorted(dirnames):
            if d in SKIP_DIRS or d.endswith(".egg-info"):
                continue
            full = Path(dirpath) / d
            if full.is_symlink():
                rel = full.relative_to(workspace).as_posix()
                try:
                    target = os.readlink(str(full))
                except OSError:
                    target = "<unreadable>"
                resolved = os.path.realpath(str(full))
                ref = _match_reference(target) or _match_reference(resolved)
                if ref or FORBIDDEN_RE.search(target) or not (resolved == ws_real or resolved.startswith(ws_real + os.sep)):
                    hits.append("%s (symlink -> %s)" % (rel, target))
                continue
            kept.append(d)
        dirnames[:] = kept

        for name in sorted(filenames):
            if name.endswith(SKIP_SUFFIXES):
                continue
            full = Path(dirpath) / name
            if full.is_symlink():
                rel = full.relative_to(workspace).as_posix()
                try:
                    target = os.readlink(str(full))
                except OSError:
                    target = "<unreadable>"
                resolved = os.path.realpath(str(full))
                ref = _match_reference(target) or _match_reference(resolved)
                if ref or FORBIDDEN_RE.search(target) or not (resolved == ws_real or resolved.startswith(ws_real + os.sep)):
                    hits.append("%s (symlink -> %s)" % (rel, target))
    return hits


def _scan_encoded_tokens(text):
    hits = []
    for match in re.finditer(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=_-]{8,}(?![A-Za-z0-9+/=_-])", text):
        token = match.group(0)
        padded = token + ("=" * ((-len(token)) % 4))
        if len(padded) % 4 != 0:
            continue
        decoders = []
        if re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", padded):
            decoders.append(lambda s: base64.b64decode(s, validate=True))
        if re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", padded):
            decoders.append(base64.urlsafe_b64decode)
        for decoder in decoders:
            try:
                raw = decoder(padded)
            except Exception:
                continue
            if _is_printable_text(raw):
                decoded = raw.decode("utf-8")
                ref = _match_reference(decoded)
                if ref:
                    hits.append("encoded reference (%s)" % ref)
                    return hits
                m = FORBIDDEN_RE.search(decoded)
                if m:
                    hits.append("encoded forbidden stack (%s)" % m.group(0))
                    return hits
            break

    for match in re.finditer(r"(?:\\x[0-9a-fA-F]{2}){6,}", text):
        hex_str = match.group(0).replace("\\x", "")
        try:
            raw = bytes.fromhex(hex_str)
        except ValueError:
            continue
        if _is_printable_text(raw):
            decoded = raw.decode("utf-8")
            ref = _match_reference(decoded)
            if ref:
                hits.append("hex-escaped reference (%s)" % ref)
                return hits
            m = FORBIDDEN_RE.search(decoded)
            if m:
                hits.append("hex-escaped forbidden stack (%s)" % m.group(0))
                return hits

    for match in re.finditer(r"(?<![0-9a-fA-F])(?:[0-9a-fA-F]{2}){8,}(?![0-9a-fA-F])", text):
        try:
            raw = bytes.fromhex(match.group(0))
        except ValueError:
            continue
        if _is_printable_text(raw):
            decoded = raw.decode("utf-8")
            ref = _match_reference(decoded)
            if ref:
                hits.append("hex reference (%s)" % ref)
                return hits
            m = FORBIDDEN_RE.search(decoded)
            if m:
                hits.append("hex forbidden stack (%s)" % m.group(0))
                return hits
    return hits


def _eval_ast_str(node, env):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return node.value
        if isinstance(node.value, bytes):
            return node.value.decode("utf-8", errors="replace")
        return None
    if isinstance(node, ast.Name):
        return env.get(node.id)
    if isinstance(node, ast.BinOp):
        left = _eval_ast_str(node.left, env)
        right = _eval_ast_str(node.right, env)
        if left is not None and right is not None:
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Div):
                return left.rstrip("/") + "/" + right.lstrip("/")
        return None
    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            elif isinstance(v, ast.FormattedValue):
                val = _eval_ast_str(v.value, env)
                if val is None:
                    return None
                parts.append(val)
            else:
                return None
        return "".join(parts)
    if isinstance(node, ast.Call):
        func = node.func
        fname = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else "")
        if fname == "chr" and len(node.args) == 1 and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, int):
            try:
                return chr(node.args[0].value)
            except ValueError:
                return None
        if fname in ("Path", "PurePosixPath", "PurePath"):
            parts = [_eval_ast_str(a, env) for a in node.args]
            if parts and all(p is not None for p in parts):
                return os.path.join(*parts).replace("\\", "/")
        if fname == "join":
            if isinstance(func, ast.Attribute):
                base_val = _eval_ast_str(func.value, env)
                if base_val is not None and len(node.args) == 1:
                    arg0 = node.args[0]
                    if isinstance(arg0, (ast.List, ast.Tuple)):
                        items = [_eval_ast_str(e, env) for e in arg0.elts]
                        if all(i is not None for i in items):
                            return base_val.join(items)
                    elif isinstance(arg0, (ast.GeneratorExp, ast.ListComp)) and len(arg0.generators) == 1:
                        comp = arg0.generators[0]
                        if (
                            isinstance(comp.target, ast.Name)
                            and isinstance(comp.iter, (ast.List, ast.Tuple))
                            and not comp.ifs
                        ):
                            items = []
                            ok = True
                            for elt in comp.iter.elts:
                                if isinstance(elt, ast.Constant):
                                    sub_env = dict(env)
                                    sub_env[comp.target.id] = elt.value
                                    if (
                                        isinstance(arg0.elt, ast.Call)
                                        and isinstance(arg0.elt.func, ast.Name)
                                        and arg0.elt.func.id == "chr"
                                        and len(arg0.elt.args) == 1
                                        and isinstance(arg0.elt.args[0], ast.Name)
                                        and arg0.elt.args[0].id == comp.target.id
                                        and isinstance(elt.value, int)
                                    ):
                                        try:
                                            items.append(chr(elt.value))
                                            continue
                                        except ValueError:
                                            ok = False
                                            break
                                ok = False
                                break
                            if ok:
                                return base_val.join(items)
            parts = [_eval_ast_str(a, env) for a in node.args]
            if parts and all(p is not None for p in parts):
                return "/".join(p.strip("/") if idx > 0 else p.rstrip("/") for idx, p in enumerate(parts))
        if fname in ("b64decode", "urlsafe_b64decode") and node.args:
            arg = _eval_ast_str(node.args[0], env)
            if arg is not None:
                try:
                    raw = base64.b64decode(arg) if fname == "b64decode" else base64.urlsafe_b64decode(arg)
                    return raw.decode("utf-8", errors="replace")
                except Exception:
                    return None
        if fname == "fromhex" and node.args:
            arg = _eval_ast_str(node.args[0], env)
            if arg is not None:
                try:
                    return bytes.fromhex(arg).decode("utf-8", errors="replace")
                except Exception:
                    return None
        if fname == "decode" and isinstance(func, ast.Attribute):
            val = _eval_ast_str(func.value, env)
            if val is not None:
                return val
            if len(node.args) >= 2:
                arg = _eval_ast_str(node.args[0], env)
                enc = _eval_ast_str(node.args[1], env)
                if arg is not None and enc and enc.lower() in ("rot_13", "rot13"):
                    return codecs.decode(arg, "rot_13")
    return None


def _scan_python_ast(text):
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    hits = []
    env = {}
    assigns = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)]
    for _ in range(3):
        for node in assigns:
            val = _eval_ast_str(node.value, env)
            if val is not None:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        env[target.id] = val
    for node in ast.walk(tree):
        val = _eval_ast_str(node, env)
        if not val:
            continue
        ref = _match_reference(val)
        if ref:
            hits.append("indirect reference via Python expression (%s)" % ref)
            return hits
        m = FORBIDDEN_RE.search(val)
        if m:
            hits.append("indirect forbidden stack via Python expression (%s)" % m.group(0))
            return hits
    return hits


def check_static(workspace, config):
    reasons = []
    workspace = Path(workspace)

    # Forbidden names anywhere in a file, symlink, archive, or indirect expression.
    hits = _check_symlinks(workspace)
    for path in iter_source_files(workspace):
        if len(hits) >= 40:
            break
        rel = path.relative_to(workspace).as_posix()
        if FORBIDDEN_RE.search(rel):
            hits.append("%s (path)" % rel)
            continue
        if path.is_symlink():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > 4 * 1024 * 1024:
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue

        # Check raw bytes so non-text / compiled files cannot hide reference paths.
        raw_ref_hit = False
        for marker in REFERENCE_MARKERS:
            if marker.encode("ascii") in raw:
                hits.append("%s: references the original at %s" % (rel, marker))
                raw_ref_hit = True
                break

        if _is_binary(raw) and not is_text_file(path):
            continue

        text = raw.decode("utf-8", errors="replace")
        for match in FORBIDDEN_RE.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            hits.append("%s:%d: %s" % (rel, line, match.group(0)))
            if len(hits) >= 40:
                break
        if len(hits) >= 40:
            break

        if not raw_ref_hit:
            collapsed = re.sub(r"""(['"])\s*(?:\+\s*)?\1""", "", text)
            ref = _match_reference(collapsed)
            if ref:
                hits.append("%s: references the original at %s" % (rel, ref))
                continue

        for enc_hit in _scan_encoded_tokens(text):
            hits.append("%s: %s" % (rel, enc_hit))
            break

        if path.suffix.lower() == ".py":
            for ast_hit in _scan_python_ast(text):
                hits.append("%s: %s" % (rel, ast_hit))
                break

    # Also check any archives left in the workspace for vendored old-stack files.
    for root_dir, dirnames, filenames in os.walk(workspace, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        for fname in filenames:
            if fname.endswith((".zip", ".whl")):
                apath = Path(root_dir) / fname
                rel = apath.relative_to(workspace).as_posix()
                try:
                    with zipfile.ZipFile(apath) as zf:
                        for member in zf.namelist():
                            if FORBIDDEN_RE.search(member) or _match_reference(member):
                                hits.append("%s: archive contains forbidden entry %s" % (rel, member))
                                break
                except Exception:
                    hits.append("%s: unreadable archive" % rel)

    if hits:
        reasons.append("forbidden stack reference: " + "; ".join(hits[:40]))

    # Legacy file patterns.
    for pattern in config.get("legacy_patterns", []):
        target = workspace / pattern
        if target.exists():
            reasons.append("legacy path still present: %s" % pattern)

    # Build metadata.
    pyproject = workspace / "pyproject.toml"
    if not pyproject.exists():
        reasons.append("pyproject.toml is missing")
    else:
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except Exception as exc:
            data = {}
            reasons.append("pyproject.toml is not valid TOML: %s" % exc)
        project = data.get("project", {})
        deps = list(project.get("dependencies", []))
        for extra in project.get("optional-dependencies", {}).values():
            deps.extend(extra)
        deps.extend(data.get("build-system", {}).get("requires", []))
        joined = " ".join(deps)
        bad = FORBIDDEN_RE.findall(joined)
        if bad:
            reasons.append("build metadata declares forbidden dependencies: %s"
                           % ", ".join(sorted(set(bad))))
        normalized = [normalize_dep(d) for d in deps]
        if not any(d.startswith("fastapi==0.115.6") for d in normalized):
            reasons.append("pyproject.toml does not pin fastapi==0.115.6")
        if not any(d.startswith("uvicorn") for d in normalized):
            reasons.append("pyproject.toml does not declare uvicorn")

    # requirements.txt, if the repository keeps one, must be clean too.
    for req in workspace.glob("requirements*.txt"):
        text = read_text(req)
        bad = FORBIDDEN_RE.findall(text)
        if bad:
            reasons.append("%s declares forbidden dependencies: %s"
                           % (req.name, ", ".join(sorted(set(bad)))))

    # Target-language code must exist and use the target stack.
    py_files = [p for p in workspace.rglob("*.py")
                if not any(part in SKIP_DIRS for part in p.parts)]
    if not py_files:
        reasons.append("no Python source files")
    else:
        uses_target = any(re.search(r"(?m)^\s*(?:import|from)\s+(?:fastapi|starlette)\b",
                                    read_text(p)) for p in py_files)
        if not uses_target:
            reasons.append("no source file imports fastapi or starlette")

    # The repository test suite is retained, renamed only where the old stack
    # is named, and keeps the original test methods.
    orig_tests = ORIGINAL / "tests" / "test_httpbin.py"
    new_tests = workspace / "tests" / "test_httpbin.py"
    if not new_tests.exists():
        reasons.append("tests/test_httpbin.py is missing")
    else:
        new_text = read_text(new_tests)
        new_names = set(re.findall(r"(?m)^\s*def (test_\w+)\s*\(", new_text))
        if "class HttpbinTestCase" not in new_text:
            reasons.append("tests/test_httpbin.py lost HttpbinTestCase")
        if orig_tests.exists():
            orig_text = read_text(orig_tests)
            orig_names = set(re.findall(r"(?m)^\s*def (test_\w+)\s*\(", orig_text))
            adapted = config.get("adapted_tests", {})
            missing = sorted(orig_names - new_names - set(adapted))
            if missing:
                reasons.append("original tests removed or renamed: %s"
                               % ", ".join(missing))
            for old, new in adapted.items():
                if new not in new_names:
                    reasons.append("replacement test for %s is missing: %s" % (old, new))
        if "pytest" not in new_text and "unittest" not in new_text:
            reasons.append("tests/test_httpbin.py does not use a test runner")

    return reasons


# ---------------------------------------------------------------------------
# Build and run
# ---------------------------------------------------------------------------


def _sandbox_kwargs():
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return {"user": SANDBOX_UID, "group": SANDBOX_GID}
    return {}


def chown_tree(path, uid=SANDBOX_UID, gid=SANDBOX_GID):
    if not (hasattr(os, "geteuid") and os.geteuid() == 0):
        return
    for dirpath, dirnames, filenames in os.walk(path):
        os.chown(dirpath, uid, gid)
        for name in filenames:
            os.chown(os.path.join(dirpath, name), uid, gid)


def copy_workspace(destination):
    ignore = shutil.ignore_patterns(
        ".git", "__pycache__", "*.pyc", "*.pyo", ".pytest_cache",
        ".mypy_cache", ".ruff_cache", ".tox", "node_modules",
        ".venv", "venv", "env", "dist", "build", "*.egg-info",
        ".than-venv", ".than-dist",
    )
    shutil.copytree(str(WORKSPACE), str(destination), ignore=ignore, symlinks=True)


def _sandbox_env(home, tmp):
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin",
        "HOME": str(home),
        "TMPDIR": str(tmp),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "PIP_NO_INDEX": "1",
        "PIP_FIND_LINKS": str(WHEELS),
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_CACHE_DIR": "1",
    }


def run_sandboxed(cmd, cwd, env, timeout):
    try:
        return subprocess.run(
            [str(part) for part in cmd], cwd=str(cwd), env=env,
            capture_output=True, text=True, timeout=timeout, **_sandbox_kwargs()
        )
    except subprocess.TimeoutExpired as exc:
        raise VerifierError("command timed out: %s" % " ".join(map(str, cmd))) from exc


def build_delivery(root):
    work = root / "workspace"
    home = root / "home"
    tmp = root / "tmp"
    run_dir = root / "run"
    venv = root / "venv"
    dist = root / "dist"
    for path in (home, tmp, run_dir, dist):
        path.mkdir(parents=True, exist_ok=True)
    copy_workspace(work)
    chown_tree(work)
    for path in (home, tmp, run_dir, dist):
        chown_tree(path)

    env = _sandbox_env(home, tmp)
    python = venv / "bin" / "python"
    pip = [python, "-m", "pip", "install", "--no-index",
           "--find-links", str(WHEELS), "--no-build-isolation"]

    result = run_sandboxed([sys.executable, "-m", "venv", str(venv)], work, env, 300)
    if result.returncode != 0:
        return {"ok": False, "stage": "venv", "log": result.stdout + result.stderr}

    result = run_sandboxed(pip + ["setuptools", "wheel"], work, env, 300)
    if result.returncode != 0:
        return {"ok": False, "stage": "build-tools", "log": result.stdout + result.stderr}

    result = run_sandboxed(pip + [".[test]"], work, env, 900)
    if result.returncode != 0:
        return {"ok": False, "stage": "install", "log": result.stdout + result.stderr}

    wheel_result = run_sandboxed(
        [python, "-m", "pip", "wheel", "--no-index", "--find-links", str(WHEELS),
         "--no-build-isolation", "--no-deps", "-w", str(dist), "."],
        work, env, 600,
    )
    if wheel_result.returncode != 0:
        return {"ok": False, "stage": "wheel", "log": wheel_result.stdout + wheel_result.stderr}

    inspect = run_sandboxed(
        [python, "-c",
         "import importlib.metadata as m, json;"
         "d=m.distribution('httpbin');"
         "print(json.dumps({'requires': d.requires or [],"
         "'dists': sorted({x.metadata['Name'].lower() for x in m.distributions() if x.metadata['Name']})}))"],
        work, env, 120,
    )
    if inspect.returncode != 0:
        return {"ok": False, "stage": "inspect", "log": inspect.stdout + inspect.stderr}

    suite = run_sandboxed([python, "-m", "pytest", "tests", "-q"], work, env, 900)
    return {
        "ok": True,
        "root": root,
        "work": work,
        "run_dir": run_dir,
        "venv_python": python,
        "env": env,
        "dist": dist,
        "inspect": json.loads(inspect.stdout.strip().splitlines()[-1]),
        "suite": suite,
        "build_log": result.stdout[-4000:],
    }


def inspect_artifact(build):
    reasons = []
    wheels = sorted(Path(build["dist"]).glob("*.whl"))
    if not wheels:
        return ["the build produced no wheel"], {}
    wheel = wheels[0]
    try:
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            metadata = [n for n in names if n.endswith(".dist-info/METADATA")]
            requires = []
            if metadata:
                text = archive.read(metadata[0]).decode("utf-8", "replace")
                requires = [line.split(":", 1)[1].strip()
                            for line in text.splitlines()
                            if line.lower().startswith("requires-dist:")]
    except zipfile.BadZipFile as exc:
        return ["the wheel is not a zip archive: %s" % exc], {}

    for name in names:
        if FORBIDDEN_RE.search(name):
            reasons.append("wheel contains a forbidden path: %s" % name)
            break
    for requirement in requires:
        bad = FORBIDDEN_RE.findall(requirement)
        if bad:
            reasons.append("wheel metadata requires a forbidden package: %s" % requirement)
    normalized = [normalize_dep(r) for r in requires]
    if not any(r.startswith("fastapi==") for r in normalized):
        reasons.append("wheel metadata does not require fastapi")
    if not any(n.startswith("httpbin/__init__.py") for n in names):
        reasons.append("wheel does not contain httpbin/__init__.py")
    top_level = set()
    for name in names:
        head = name.split("/", 1)[0]
        if head and not head.endswith(".dist-info") and head != "httpbin":
            top_level.add(head)
    if top_level:
        reasons.append("wheel ships unexpected top-level files: %s"
                       % ", ".join(sorted(top_level)))
    return reasons, {"wheel": wheel.name, "requires": requires,
                     "top_level": sorted(top_level)}


def check_installed(build):
    reasons = []
    info = build["inspect"]
    dists = {d.lower() for d in info.get("dists", [])}
    bad = sorted(dists & set(FORBIDDEN))
    if bad:
        reasons.append("delivery environment installs forbidden packages: %s"
                       % ", ".join(bad))
    requires = info.get("requires", [])
    for requirement in requires:
        found = FORBIDDEN_RE.findall(requirement)
        if found:
            reasons.append("installed metadata requires a forbidden package: %s"
                           % requirement)
    if not any(normalize_dep(r).startswith("fastapi==") for r in requires):
        reasons.append("installed metadata does not require fastapi")
    return reasons


def start_server(cmd, cwd, env, log_path, as_sandbox):
    kwargs = _sandbox_kwargs() if as_sandbox else {}
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_bytes(b"")
    if as_sandbox:
        chown_tree(log_path.parent)
    handle = open(str(log_path), "ab", buffering=0)
    try:
        return subprocess.Popen(
            [str(part) for part in cmd], cwd=str(cwd), env=env,
            stdout=handle, stderr=subprocess.STDOUT,
            start_new_session=True, **kwargs
        )
    finally:
        handle.close()


def stop_server(process):
    if process is None:
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


def port_is_free():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", PORT))
            return True
        except OSError:
            return False


def wait_ready(process, log_path, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            log = read_text(log_path)[-3000:]
            raise VerifierError("server exited with %s\n%s" % (process.returncode, log))
        try:
            with urllib.request.urlopen(BASE + "/get", timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.2)
    log = read_text(log_path)[-3000:]
    raise VerifierError("server did not become ready\n%s" % log)


def run_differential(root, build):
    probes = probe_registry()
    original_log = root / "logs" / "original.log"
    delivery_log = root / "logs" / "delivery.log"

    if not port_is_free():
        raise VerifierError("port %d is already in use" % PORT)

    original_env = {
        "PATH": "/opt/orig-venv/bin:/usr/local/bin:/usr/bin:/bin",
        "HOME": "/root",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PORT": str(PORT),
    }
    original = start_server(["/bin/sh", str(ORIG_START)], ORIGINAL,
                            original_env, original_log, as_sandbox=False)
    try:
        wait_ready(original, original_log)
        original_records = record_all(BASE, probes)
    finally:
        stop_server(original)

    for _ in range(50):
        if port_is_free():
            break
        time.sleep(0.1)

    delivery = start_server(
        [build["venv_python"], "-m", "uvicorn", "httpbin:app",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        build["run_dir"], build["env"], delivery_log, as_sandbox=True,
    )
    startup_error = None
    try:
        try:
            wait_ready(delivery, delivery_log)
        except VerifierError as exc:
            startup_error = str(exc)
        if startup_error is None:
            delivery_records = record_all(BASE, probes, tolerate=True)
        else:
            delivery_records = {}
    finally:
        stop_server(delivery)

    logs = {
        "original_log": read_text(original_log)[-2000:],
        "delivery_log": read_text(delivery_log)[-3000:],
    }
    if startup_error is not None:
        # A delivery that does not start is the agent's failure, not ours.
        groups = {}
        for name, description in GROUPS.items():
            ids = [p["id"] for p in probes if p["group"] == name]
            groups[name] = {
                "description": description,
                "passed": False,
                "total": len(ids),
                "failed": ids,
                "details": {"startup": [startup_error[-800:]]},
            }
        return groups, 0.0, logs

    results = {}
    for probe in probes:
        pid = probe["id"]
        left = original_records[pid]
        right = delivery_records[pid]
        problems = diff_records(left, right, probe)
        results[pid] = problems

    groups = {}
    for name, description in GROUPS.items():
        ids = [p["id"] for p in probes if p["group"] == name]
        failed = [pid for pid in ids if results.get(pid)]
        groups[name] = {
            "description": description,
            "passed": not failed,
            "total": len(ids),
            "failed": failed,
            "details": {pid: results[pid][:6] for pid in failed},
        }
    passed = sum(1 for group in groups.values() if group["passed"])
    return groups, passed / float(len(groups)), logs


# ---------------------------------------------------------------------------
# Absent gate
# ---------------------------------------------------------------------------


def absent_gate(workspace):
    workspace = Path(workspace)
    if not (workspace / "pyproject.toml").exists():
        return True, "pyproject.toml is missing"
    py_files = [p for p in workspace.rglob("*.py")
                if not any(part in SKIP_DIRS for part in p.parts)]
    if not py_files:
        return True, "no Python source files"
    target = any(
        re.search(r"(?m)^\s*(?:import|from)\s+(?:fastapi|starlette)\b", read_text(p))
        for p in py_files
    )
    if not target:
        return True, "no FastAPI or Starlette source"
    try:
        data = tomllib.loads((workspace / "pyproject.toml").read_text(encoding="utf-8"))
    except Exception:
        data = {}
    deps = list(data.get("project", {}).get("dependencies", []))
    for extra in data.get("project", {}).get("optional-dependencies", {}).values():
        deps.extend(extra)
    if any(FORBIDDEN_RE.search(dep) for dep in deps):
        return True, "the build metadata still declares the source stack"
    return False, "migration present"


# ---------------------------------------------------------------------------
# Judge evidence and scoring
# ---------------------------------------------------------------------------


def _relative_files(root):
    root = Path(root)
    files = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in DIFF_SKIP_DIRS)
        for name in filenames:
            if name.endswith((".pyc", ".pyo")):
                continue
            files.add((Path(dirpath) / name).relative_to(root).as_posix())
    return files


def _is_binary(data):
    if b"\x00" in data[:8000]:
        return True
    try:
        data.decode("utf-8")
        return False
    except UnicodeDecodeError:
        return True


def make_changes_diff(original, workspace, limit=MAX_JUDGE_DIFF):
    original = Path(original)
    workspace = Path(workspace)
    left_files = _relative_files(original)
    right_files = _relative_files(workspace)
    chunks = []
    total = 0
    for rel in sorted(left_files | right_files):
        left_path = original / rel
        right_path = workspace / rel
        if rel in left_files and rel in right_files:
            left_data = left_path.read_bytes()
            right_data = right_path.read_bytes()
            if left_data == right_data:
                continue
            if _is_binary(left_data) or _is_binary(right_data):
                chunk = "diff --git a/%s b/%s\nBinary files a/%s and b/%s differ\n" % (
                    rel, rel, rel, rel)
            else:
                left_lines = left_data.decode("utf-8", "replace").splitlines(True)
                right_lines = right_data.decode("utf-8", "replace").splitlines(True)
                diff = difflib.unified_diff(
                    left_lines, right_lines,
                    fromfile="a/%s" % rel, tofile="b/%s" % rel, n=3,
                )
                chunk = "diff --git a/%s b/%s\n" % (rel, rel) + "".join(diff)
        elif rel in right_files:
            data = right_path.read_bytes()
            if _is_binary(data):
                chunk = "diff --git a/%s b/%s\nnew binary file\n" % (rel, rel)
            else:
                lines = data.decode("utf-8", "replace").splitlines(True)
                diff = difflib.unified_diff(
                    [], lines, fromfile="/dev/null", tofile="b/%s" % rel, n=3,
                )
                chunk = "diff --git a/%s b/%s\nnew file mode 100644\n" % (rel, rel) + "".join(diff)
        else:
            data = left_path.read_bytes()
            if _is_binary(data):
                chunk = "diff --git a/%s b/%s\ndeleted binary file\n" % (rel, rel)
            else:
                lines = data.decode("utf-8", "replace").splitlines(True)
                diff = difflib.unified_diff(
                    lines, [], fromfile="a/%s" % rel, tofile="/dev/null", n=3,
                )
                chunk = "diff --git a/%s b/%s\ndeleted file mode 100644\n" % (rel, rel) + "".join(diff)
        if not chunk.endswith("\n"):
            chunk += "\n"
        total += len(chunk.encode("utf-8"))
        if total > limit:
            raise VerifierError("judge evidence exceeds 1 MiB")
        chunks.append(chunk)
    return "".join(chunks)


def write_judge_evidence(out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    diff = make_changes_diff(ORIGINAL, WORKSPACE)
    (out_dir / "changes.diff").write_text(diff, encoding="utf-8")
    trajectory = Path("/logs/agent/trajectory.json")
    if trajectory.exists():
        shutil.copyfile(str(trajectory), str(out_dir / "trajectory.json"))
    else:
        (out_dir / "trajectory.json").write_text(
            json.dumps({"steps": []}), encoding="utf-8")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        os.chmod(str(out_dir), 0o700)
        for path in out_dir.iterdir():
            os.chmod(str(path), 0o600)
    return out_dir


def _judge_prompt(criteria, diff, trajectory, compact=False):
    lines = [
        "You are grading a repository migration task (httpbin Flask/Werkzeug/gunicorn -> FastAPI/Uvicorn).",
        "Answer every criterion with \"yes\" or \"no\". Answer \"no\" if evidence is missing or unclear.",
        "",
        "Criteria:",
    ]
    for item in criteria:
        desc = item["description"]
        if compact:
            desc = " ".join(desc.splitlines()[:2])
        lines.append("%s. %s: %s" % (item["id"], item.get("name", ""), desc))
    lines += [
        "",
        "Return one JSON object mapping each criterion id to \"yes\" or \"no\".",
        "",
        "=== changes.diff ===",
        diff,
        "=== trajectory.json ===",
        trajectory,
    ]
    return "\n".join(lines)


def _compact_diff_for_local(diff_text, max_chars=2500):
    if len(diff_text) <= max_chars:
        return diff_text
    headers = [line for line in diff_text.splitlines() if line.startswith("--- ") or line.startswith("+++ ")]
    manifest = "Changed files:\n" + "\n".join(headers) + "\n\n"
    remaining = max(500, max_chars - len(manifest))
    return manifest + diff_text[:remaining]


def run_judge(judge_dir, criteria):
    config = tomllib.loads(JUDGE_PATH.read_text(encoding="utf-8"))
    judge_cfg = config.get("judge", {})
    base_url = (
        os.environ.get("OPENAI_BASE_URL", "").strip().rstrip("/")
        or str(judge_cfg.get("base_url", "")).strip().rstrip("/")
    )
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key and not base_url:
        raise VerifierError("OPENAI_API_KEY is not set")
    model = os.environ.get("JUDGE_MODEL", "").strip() or judge_cfg.get("judge", "openai/gpt-4.1")
    if "/" in model:
        model = model.split("/", 1)[1]
    judge_dir = Path(judge_dir)
    diff = read_text(judge_dir / "changes.diff")[:MAX_JUDGE_DIFF]
    trajectory = read_text(judge_dir / "trajectory.json")[:200000]
    compact = bool(base_url)
    if compact:
        diff = _compact_diff_for_local(diff, 2500)
        trajectory = trajectory[:500]
    payload = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "You are a strict, fair code reviewer."},
            {"role": "user", "content": _judge_prompt(criteria, diff, trajectory, compact=compact)},
        ],
    }
    endpoint = (base_url + "/chat/completions") if base_url else "https://api.openai.com/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key or base_url:
        headers["Authorization"] = "Bearer %s" % (api_key or "ollama")
    data = json.dumps(payload).encode("utf-8")
    timeout_sec = 600 if base_url else 180
    last_exc = None
    body = None
    for attempt in range(3):
        request = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout_sec) as response:
                body = json.loads(response.read().decode("utf-8"))
            break
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last_exc = exc
            time.sleep(2)
    if body is None:
        raise VerifierError("judge request failed: %s" % last_exc) from last_exc
    try:
        content = body["choices"][0]["message"]["content"]
        answers = json.loads(content)
    except (KeyError, IndexError, ValueError) as exc:
        raise VerifierError("judge returned an unusable answer: %s" % exc) from exc
    verdict = {}
    for item in criteria:
        value = str(answers.get(item["id"], "no")).strip().lower()
        verdict[item["id"]] = value == "yes"
    score = sum(1 for value in verdict.values() if value) / float(len(criteria))
    return score, verdict


# ---------------------------------------------------------------------------
# Verdict and reward
# ---------------------------------------------------------------------------


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(str(temporary), str(path))


def validate_manifest():
    """Fail loudly if config.json and the embedded registry disagree."""
    config = load_config()
    probes = probe_registry()
    ids = [p["id"] for p in probes]
    if len(ids) != len(set(ids)):
        raise VerifierError("the probe registry contains duplicate ids")
    if set(config.get("test_methods", [])) != set(ids):
        raise VerifierError("config.json test_methods do not match the registry")
    if set(config.get("groups", {})) != set(GROUPS):
        raise VerifierError("config.json group set does not match the registry")
    grouped = []
    for group in config.get("groups", {}).values():
        grouped.extend(group.get("tests", []))
    if sorted(grouped) != sorted(ids):
        raise VerifierError("config.json groups do not partition the probes")
    return config


def measure(out_path):
    config = validate_manifest()
    reasons = check_static(WORKSPACE, config)
    verdict = {
        "gate": not reasons,
        "gate_reasons": reasons,
        "groups": {},
        "behaviour": 0.0,
        "suite": {},
        "artifact": {},
        "logs": {},
    }
    if reasons:
        for name, description in GROUPS.items():
            ids = [p["id"] for p in probe_registry() if p["group"] == name]
            verdict["groups"][name] = {
                "description": description, "passed": False,
                "total": len(ids), "failed": ids, "details": {},
            }
        write_json(out_path, verdict)
        return verdict

    root = Path(tempfile.mkdtemp(prefix="than-measure-", dir="/tmp"))
    os.chmod(str(root), 0o700)
    chown_tree(root)
    try:
        build = build_delivery(root)
        if not build["ok"]:
            verdict["gate"] = False
            verdict["gate_reasons"].append(
                "delivery build failed at %s: %s"
                % (build["stage"], build["log"][-2000:]))
            for name, description in GROUPS.items():
                ids = [p["id"] for p in probe_registry() if p["group"] == name]
                verdict["groups"][name] = {
                    "description": description, "passed": False,
                    "total": len(ids), "failed": ids, "details": {},
                }
            write_json(out_path, verdict)
            return verdict

        artifact_reasons, artifact_info = inspect_artifact(build)
        installed_reasons = check_installed(build)
        suite = build["suite"]
        suite_ok = suite.returncode == 0
        suite_text = suite.stdout + suite.stderr
        summary_line = ""
        for line in reversed([ln for ln in suite_text.splitlines() if ln.strip()]):
            summary_line = line.strip()
            break
        if suite_ok and " skipped" in summary_line:
            raise VerifierError("the repository test suite skipped tests: %s"
                                % summary_line)
        if suite_ok and "passed" not in summary_line:
            raise VerifierError("the repository test suite did not run: %s"
                                % summary_line)
        verdict["artifact"] = artifact_info
        verdict["suite"] = {
            "passed": suite_ok,
            "returncode": suite.returncode,
            "summary": (suite.stdout + suite.stderr)[-3000:],
        }
        reasons = artifact_reasons + installed_reasons
        if not suite_ok:
            reasons.append("the repository test suite fails: %s"
                           % (suite.stdout + suite.stderr)[-2000:])
        if reasons:
            verdict["gate"] = False
            verdict["gate_reasons"].extend(reasons)
            for name, description in GROUPS.items():
                ids = [p["id"] for p in probe_registry() if p["group"] == name]
                verdict["groups"][name] = {
                    "description": description, "passed": False,
                    "total": len(ids), "failed": ids, "details": {},
                }
            write_json(out_path, verdict)
            return verdict

        groups, behaviour, logs = run_differential(root, build)
        verdict["groups"] = groups
        verdict["behaviour"] = round(behaviour, 6)
        verdict["logs"] = logs
        write_json(out_path, verdict)
        return verdict
    finally:
        shutil.rmtree(str(root), ignore_errors=True)


def score(verdict_path, out_path, judge_dir=None, use_judge=True):
    with open(verdict_path, "r", encoding="utf-8") as handle:
        verdict = json.load(handle)
    if not verdict.get("gate"):
        write_json(out_path, {
            "reward": 0.0,
            "behaviour": 0.0,
        })
        return 0.0
    behaviour = float(verdict.get("behaviour", 0.0))
    judge_score = None
    judge_answers = None
    if use_judge:
        config = tomllib.loads(JUDGE_PATH.read_text(encoding="utf-8"))
        criteria = config.get("criterion", [])
        judge_score, judge_answers = run_judge(judge_dir, criteria)
        reward = 0.8 * behaviour + 0.2 * judge_score
    else:
        reward = behaviour
    numeric_payload = {
        "reward": round(reward, 6),
        "behaviour": round(behaviour, 6),
    }
    if judge_score is not None:
        numeric_payload["judge"] = round(judge_score, 6)
    write_json(out_path, numeric_payload)
    details_payload = {
        "reward": round(reward, 6),
        "behaviour": round(behaviour, 6),
        "judge": None if judge_score is None else round(judge_score, 6),
        "gate": True,
        "groups": {name: group["passed"]
                   for name, group in verdict.get("groups", {}).items()},
    }
    if judge_answers is not None:
        details_payload["judge_answers"] = judge_answers
    details = Path(out_path).with_name("reward-details.json")
    write_json(details, details_payload)
    return reward


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--absent-gate", action="store_true")
    parser.add_argument("--measure", action="store_true")
    parser.add_argument("--judge-evidence", action="store_true")
    parser.add_argument("--score", action="store_true")
    parser.add_argument("--list-groups", action="store_true")
    parser.add_argument("--selfcheck", action="store_true")
    parser.add_argument("--out")
    parser.add_argument("--out-dir")
    parser.add_argument("--verdict")
    parser.add_argument("--judge-dir")
    parser.add_argument("--no-judge", action="store_true")
    args = parser.parse_args(argv)

    if args.list_groups:
        probes = probe_registry()
        payload = {"groups": {}}
        for name, description in GROUPS.items():
            payload["groups"][name] = {
                "description": description,
                "tests": [p["id"] for p in probes if p["group"] == name],
            }
        payload["test_methods"] = [p["id"] for p in probes]
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.selfcheck:
        config = load_config()
        probes = probe_registry()
        ids = [p["id"] for p in probes]
        assert len(ids) == len(set(ids)), "duplicate probe id"
        configured = config.get("test_methods", [])
        assert set(configured) == set(ids), "config.json test_methods mismatch"
        grouped = []
        for name, group in config.get("groups", {}).items():
            grouped.extend(group.get("tests", []))
            assert group.get("tests"), "empty group %s" % name
        assert sorted(grouped) == sorted(ids), "config.json groups do not partition the probes"
        assert set(config.get("groups", {})) == set(GROUPS), "group set mismatch"
        print("selfcheck ok: %d probes in %d groups" % (len(ids), len(GROUPS)))
        return 0

    if args.absent_gate:
        absent, reason = absent_gate(WORKSPACE)
        print(json.dumps({"absent": absent, "reason": reason}))
        return 3 if absent else 0

    if args.measure:
        if not args.out:
            parser.error("--measure needs --out")
        try:
            verdict = measure(args.out)
        except VerifierError as exc:
            print("verifier error: %s" % exc, file=sys.stderr)
            return 1
        print(json.dumps({
            "gate": verdict["gate"],
            "gate_reasons": verdict["gate_reasons"],
            "behaviour": verdict["behaviour"],
            "groups": {name: group["passed"]
                       for name, group in verdict["groups"].items()},
        }, indent=2))
        return 0

    if args.judge_evidence:
        if not args.out_dir:
            parser.error("--judge-evidence needs --out-dir")
        try:
            write_judge_evidence(args.out_dir)
        except VerifierError as exc:
            print("verifier error: %s" % exc, file=sys.stderr)
            return 1
        return 0

    if args.score:
        if not args.verdict or not args.out:
            parser.error("--score needs --verdict and --out")
        use_judge = not args.no_judge and os.environ.get("REWARDKIT_DISABLE_JUDGE") != "1"
        try:
            reward = score(args.verdict, args.out, args.judge_dir, use_judge)
        except VerifierError as exc:
            print("verifier error: %s" % exc, file=sys.stderr)
            return 1
        print(json.dumps({"reward": reward}))
        return 0

    parser.error("no action requested")


if __name__ == "__main__":
    sys.exit(main())
