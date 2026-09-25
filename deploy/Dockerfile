# syntax=docker/dockerfile:1
#
# The Pi runs this image and nothing else. Its job is to make the box immune to
# OS-level Python drift: Raspberry Pi OS upgrades its system Python on its own
# schedule, and a scorekeeper that stops booting because Bookworm moved to 3.12
# is a scorekeeper nobody trusts. Everything the app needs is pinned in here.
#
# Three stages, because the two toolchains have nothing to say to each other.
# Node builds the frontend and is then discarded entirely; uv resolves the
# Python environment and is discarded too. The runtime stage inherits a built
# virtualenv and a directory of static assets, and carries neither npm nor uv
# nor a compiler -- which is most of the image size and most of the CVE surface
# that would otherwise sit on the Pi for years between deploys.

# --------------------------------------------------------------------------
# Stage 1: the frontend.
# --------------------------------------------------------------------------
# Node 22 because .nvmrc says 22 and CI reads that same file via
# `node-version-file`. Building the shipped assets on a different major than
# the one the tests ran under would mean the build we ship is not the build
# that was verified.
FROM node:22-slim AS frontend

WORKDIR /build

# package.json and the lockfile alone, so that `npm ci` -- the slowest step in
# this stage by an order of magnitude -- is cached against dependency changes
# rather than against every source edit.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# `npm run build` is `tsc -b && vite build`, and tsconfig.json references the
# test project alongside the app, so this needs the whole source tree rather
# than only what Vite would bundle. node_modules is kept out by .dockerignore,
# so this copy cannot clobber what `npm ci` just installed.
COPY frontend/ ./
RUN npm run build

# --------------------------------------------------------------------------
# Stage 2: the Python environment.
# --------------------------------------------------------------------------
FROM python:3.11-slim AS backend

# Pinned rather than :latest. --frozen exists to make the dependency set
# reproducible; resolving it with an unpinned resolver would undercut that.
COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /usr/local/bin/uv

# copy: the cache and the target are different filesystems under BuildKit, and
# uv's default hardlink strategy warns on every package otherwise.
# never: the lockfile is resolved for the interpreter in this base image, and a
# silent uv-managed download would make the runtime stage's python the wrong one.
ENV UV_LINK_MODE=copy \
  UV_COMPILE_BYTECODE=1 \
  UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first and the project second, for the same caching reason as
# npm ci above: application code changes on every commit, the lockfile rarely.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project

# --no-dev: pytest, mypy and ruff have no business on the Pi.
# --no-editable: an editable install would leave the venv pointing at /app/backend,
# a path the runtime stage does not copy. This puts a real copy in site-packages,
# so the runtime stage needs the virtualenv and nothing else.
COPY backend/ ./backend/
RUN uv sync --frozen --no-dev --no-editable

# --------------------------------------------------------------------------
# Stage 3: what actually runs.
# --------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

# Fixed 1000:1000 rather than a build arg, because the number has to agree with
# something outside this image: the bind mounts on the host. On Raspberry Pi OS
# the first user -- `pi` -- is uid 1000, so files this container writes to
# /var/lib/darts stay readable and editable over SSH, by the host's sqlite3 CLI,
# and by Samba in #30. That is the whole reason #28 specifies bind mounts
# instead of named volumes, and a per-host build arg would trade it away for
# flexibility a single-Pi deployment never uses.
# bootstrap-pi.sh chowns the bind mounts to match.
RUN groupadd --gid 1000 darts \
  && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin darts

WORKDIR /app

COPY --from=backend /app/.venv /app/.venv
COPY --from=frontend /build/dist /app/static
COPY deploy/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# Activating the virtualenv the way a shell would, so `uvicorn` and the
# darts-* console scripts resolve without a wrapper.
ENV PATH="/app/.venv/bin:${PATH}" \
  PYTHONUNBUFFERED=1 \
  DARTS_STATIC_DIR=/app/static \
  DARTS_DB_PATH=/var/lib/darts/darts.db

# The build stamp /api/healthz and /api/version report. config.py defaults it
# to "unknown", which is the honest answer for a source checkout; an image is
# built from a known commit and should say so.
ARG GIT_SHA=unknown
ENV DARTS_GIT_SHA=${GIT_SHA}

USER 1000:1000

EXPOSE 8000

# No curl and no wget: python:3.11-slim ships neither, and installing one would
# add an apt layer and a CVE surface for the sake of one HTTP request every
# thirty seconds. urllib is already here, in the interpreter the app runs on.
#
# urlopen raises on any non-2xx, so the 503 that /api/healthz returns when the
# database is degraded or the card has gone read-only becomes an unhealthy
# container. Note what that does and does not buy: `restart: unless-stopped`
# restarts a container that *exits*, not one that is merely unhealthy, so this
# is observability -- `docker ps` tells you the box is sick -- and not recovery.
# See deploy/compose.yaml.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/api/healthz' % (os.environ.get('DARTS_PORT') or '8000'),timeout=4)"]

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
