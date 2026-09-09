# praesidia (Python SDK) — build + test image.
# Purpose: CI / reproducible builds and an import smoke check. This is NOT a
# deployable service — the package is a wheel published to PyPI. The image
# exists so the build + test pipeline is reproducible on any host.
#
# Base: python:3.14-slim. Runs as a non-root user.

FROM python:3.14-slim@sha256:d3400aa122fa42cf0af0dbe8ec3091b047eac5c8f7e3539f7135e86d855dc015 AS build
# python:3.14-slim
WORKDIR /app
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY praesidia ./praesidia
COPY tests ./tests
COPY test-fixtures ./test-fixtures
COPY examples ./examples
COPY .github/workflows/publish.yml ./.github/workflows/publish.yml
# Install with dev extras, run import + base SDK compatibility tests, then build
# the exact runtime wheelhouse. The final stage consumes /wheels, which makes
# this gate load-bearing (an unreferenced test stage is pruned by BuildKit).
# Required CI frameworks job separately enforces full-source 90% coverage on
# Python3.11, where the pinned native runtimes are supported and installed.
RUN pip install "uv==0.5.24" \
 && uv sync --frozen --extra dev --extra langgraph \
 && .venv/bin/python -c "from praesidia import Praesidia; print('SDK import OK')" \
 && .venv/bin/python -m pytest -q \
 && uv export --frozen --no-dev --no-emit-project --format requirements-txt --no-hashes --output-file /tmp/runtime-requirements.txt \
 && pip wheel --wheel-dir /wheels -r /tmp/runtime-requirements.txt \
 && uv build --wheel --out-dir /wheels

# ---- runtime: minimal, non-root, installs only the package (no dev/test deps) ----
FROM python:3.14-slim@sha256:d3400aa122fa42cf0af0dbe8ec3091b047eac5c8f7e3539f7135e86d855dc015 AS runtime
# python:3.14-slim
WORKDIR /app
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN useradd --create-home --uid 10001 praesidia
COPY --from=build /wheels /wheels
# Install only the artifacts resolved by the tested build stage. This avoids a
# second, potentially different dependency resolution in the runtime stage.
RUN pip install --no-index --find-links=/wheels praesidia \
 && rm -rf /wheels
USER praesidia
# Smoke: import the SDK entrypoint.
CMD ["python", "-c", "from praesidia import Praesidia; print('praesidia import OK')"]
