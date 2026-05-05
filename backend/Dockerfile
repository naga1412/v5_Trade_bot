# syntax=docker/dockerfile:1.6
FROM python:3.11-slim-bookworm AS base

# Install system deps (existing) + wget for the TA-Lib source-build fallback
# (only triggered if pip wheel ever becomes unavailable for this base image).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    libpq-dev \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Install TA-Lib C library v0.6.4 from upstream tarball as a defensive
# fallback. The Python wheel bundles its own copy of the C lib for
# manylinux_2_28_x86_64 + cp311, so this step is a no-op for the happy
# path; it only becomes relevant if pip ever falls back to an sdist build
# (e.g., on an unsupported arch). apt's `ta-lib0` is stuck at 0.4 on
# bookworm, which is incompatible with the Python 0.6.x wrapper. Upstream
# skipped tagging v0.6.0 of the C library; v0.6.4 is the latest 0.6.x and
# is API-compatible with the Python 0.6.x wrapper.
RUN cd /tmp \
    && wget -q https://github.com/TA-Lib/ta-lib/releases/download/v0.6.4/ta-lib-0.6.4-src.tar.gz \
    && tar -xzf ta-lib-0.6.4-src.tar.gz \
    && cd ta-lib-0.6.4 \
    && ./configure --prefix=/usr \
    && make -j$(nproc) \
    && make install \
    && cd / && rm -rf /tmp/ta-lib-0.6.4*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml ./
# Install torch CPU-only wheel BEFORE the project install so pip's resolver
# uses the already-installed CPU build (~150 MB) instead of pulling the
# default wheel (~1.5 GB with CUDA libs). torch==2.4.1 must match the
# pyproject.toml pin so `pip install -e .` is a no-op for torch.
RUN pip install --upgrade pip \
    && pip install --index-url https://download.pytorch.org/whl/cpu torch==2.4.1 \
    && pip install -e ".[dev]"

COPY . .

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl --fail http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
