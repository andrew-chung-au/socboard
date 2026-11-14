# The container image of SOCBoard: the API and the built frontend, served from one origin (#190).
# CI builds it (.github/workflows/image.yml); the agent's sandbox has no Docker.
#
# Rules the static test (tests/test_dockerfile.py) holds:
# - one worker: the start command has no --workers and no WEB_CONCURRENCY is set, because the
#   in-process limits of #178 and #183 assume one worker (decision 9 of the Deploy record);
# - the start command passes --allow-non-loopback-host (#142), the only thing that lets the
#   server bind SOCBOARD_HOST=0.0.0.0;
# - FORWARDED_ALLOW_IPS and SOCBOARD_TRUSTED_PROXIES are not set here: whoever runs the container
#   sets SOCBOARD_TRUSTED_PROXIES to the proxy's address (README, "Login attempt limits"), and
#   if they set FORWARDED_ALLOW_IPS it lists only that proxy, never "*";
# - no secret setting is named in an ENV or ARG line, the last stage is not root, and the
#   frontend is built with the mock off and VITE_API_BASE_URL unset (the client uses /api).

# VERIFY: on the first CI build that each tag below exists and the digest is the index digest
# (digests read from the registries on 2026-11-14).

# Stage 1: build the frontend. Node runs vite; bun installs and runs the scripts, as in verify.yml.
FROM oven/bun:1.4.2-slim@sha256:cb3bbbb08e13a4a2ff400f24c7a2a1d5efa83f6ef8544d52d95a519631e2fc61 AS bun

FROM node:24.22.0-bookworm-slim@sha256:91acba91b842bec0bf7ea6f5e09c644c7ccad72c4996ed9616d861d1c7cd60d4 AS frontend
COPY --from=bun /usr/local/bin/bun /usr/local/bin/bun
# An ENV, not an ARG: an image cannot be built with the mock on.
ENV VITE_USE_MOCK=false
ARG VITE_FEATURE_REASSIGN
ARG VITE_POLL_INTERVAL_MS
WORKDIR /build/frontend
COPY frontend/package.json frontend/bun.lock frontend/bunfig.toml ./
RUN bun install --frozen-lockfile
COPY frontend/ ./
RUN bun run build

# Stage 2: the app. The Python minor version is the one .python-version names (#100).
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
COPY --from=ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 /uv /usr/local/bin/uv

ENV UV_PYTHON_PREFERENCE=only-system \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH \
    SOCBOARD_HOST=0.0.0.0

WORKDIR /app
# The locked runtime dependencies only (no dev group). pyproject.toml names README.md.
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY socboard/ ./socboard/
COPY --from=frontend /build/frontend/dist ./frontend/dist

# A numeric non-root user.
RUN groupadd --system --gid 10001 socboard \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin socboard
USER 10001:10001

EXPOSE 8000
# One worker. python -m socboard.migrate and python -m socboard.seed are the one-shot commands.
CMD ["python", "-m", "socboard.server", "--allow-non-loopback-host"]
