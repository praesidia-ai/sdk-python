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
COPY pyproject.toml README.md LICENSE ./
COPY praesidia ./praesidia
COPY tests ./tests
# Install with dev extras, then run the import smoke + full pytest suite.
RUN pip install ".[dev]" \
 && python -c "from praesidia import Praesidia; print('SDK import OK')" \
 && pytest -q

# ---- runtime: minimal, non-root, installs only the package (no dev/test deps) ----
FROM python:3.14-slim@sha256:d3400aa122fa42cf0af0dbe8ec3091b047eac5c8f7e3539f7135e86d855dc015 AS runtime
# python:3.14-slim
WORKDIR /app
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN useradd --create-home --uid 10001 praesidia
COPY pyproject.toml README.md LICENSE ./
COPY praesidia ./praesidia
RUN pip install .
USER praesidia
# Smoke: import the SDK entrypoint.
CMD ["python", "-c", "from praesidia import Praesidia; print('praesidia import OK')"]
