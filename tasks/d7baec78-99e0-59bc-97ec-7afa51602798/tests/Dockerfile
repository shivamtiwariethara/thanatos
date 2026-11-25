# Verifier image for the httpbin Flask -> FastAPI migration task.
#
# It holds three things the agent never sees at run time:
#   * /opt/original      the pristine repository, execute-only for root
#   * /opt/orig-venv     the original Flask/gunicorn stack, root-only
#   * /opt/wheels        offline caches (the target cache is world-readable so
#                        the unprivileged sandbox can install the delivery)
#
# The hidden suite lives in /tests, mode 700.

FROM python:3.11-slim-bookworm@sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates git procps \
 && rm -rf /var/lib/apt/lists/*

# Offline caches.  The original stack is split away from the target stack so
# the sandbox cannot resolve Flask by accident.
COPY wheels/orig /opt/wheels/orig
COPY wheels/target /opt/wheels/target

# The pristine original and a pre-built Flask/gunicorn virtualenv.  httpbin's
# core module reads its own distribution metadata, so the original has to be
# installed, not just present on sys.path.
COPY original /opt/original
COPY requirements-original.txt /opt/requirements-original.txt
RUN python -m venv /opt/orig-venv \
 && /opt/orig-venv/bin/pip install --no-cache-dir --no-index \
        --find-links /opt/wheels/orig -r /opt/requirements-original.txt \
 && /opt/orig-venv/bin/pip install --no-cache-dir --no-index \
        --find-links /opt/wheels/orig setuptools wheel \
 && cp -a /opt/original /tmp/orig-build \
 && /opt/orig-venv/bin/pip install --no-cache-dir --no-index \
        --no-build-isolation --no-deps /tmp/orig-build \
 && rm -rf /tmp/orig-build
COPY start-original.sh /opt/original/start.sh
RUN chmod +x /opt/original/start.sh

# HTTP client used by the verifier itself: gzip/deflate/brotli decoding.
RUN python -m pip install --no-cache-dir --no-index \
        --find-links /opt/wheels/target httpx brotlicffi

# Unprivileged account used to build and run the delivery.
RUN useradd --create-home --uid 1000 --shell /bin/bash agent

# The hidden suite.  Everything is root-only; the sandbox only ever gets a
# private copy of /workspace under /tmp.
COPY . /tests/
RUN rm -rf /tests/original /tests/wheels /tests/__pycache__ /tests/work-groups.json \
 && chmod -R go-rwx /opt/original /opt/orig-venv /opt/wheels/orig \
 && chmod -R go+rX /opt/wheels/target \
 && chmod 700 /tests \
 && chmod -R go-rwx /tests

WORKDIR /tests

CMD ["bash", "/tests/test.sh"]
