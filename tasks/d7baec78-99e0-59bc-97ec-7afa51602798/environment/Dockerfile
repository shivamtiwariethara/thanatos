# Agent environment for the httpbin Flask -> FastAPI migration task.
#
# Everything the agent needs is baked in: the pristine repository at the base
# commit, a runnable copy of the original Flask service, and an offline wheel
# cache for the FastAPI target stack.  No network access is required (or
# allowed) at run time.

FROM python:3.11-slim-bookworm@sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends git tmux \
 && rm -rf /var/lib/apt/lists/*

# Offline wheel caches, split so that the original stack is never on the
# default pip search path.
COPY wheels/orig /opt/wheels/orig
COPY wheels/target /opt/wheels/target

# Runnable original: source plus a pre-built Flask/gunicorn virtualenv.
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

# The agent's repository: an exact copy of the pristine tree at the base
# commit, with no history and no pre-existing build output.
RUN mkdir -p /workspace \
 && cp -a /opt/original/. /workspace/ \
 && rm -rf /workspace/.git

COPY start-original.sh /opt/original/start.sh
RUN chmod +x /opt/original/start.sh

# Offline, pinned installs for the delivery build.
ENV PIP_NO_INDEX=1 \
    PIP_FIND_LINKS=/opt/wheels/target

# Unprivileged agent account.
RUN useradd --create-home --uid 1000 --shell /bin/bash agent \
 && chown -R agent:agent /workspace

WORKDIR /workspace
USER agent

ENTRYPOINT []
CMD ["bash"]
