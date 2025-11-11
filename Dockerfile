# Dockerfile for Hybrid Outbound Agent (Finn)
# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.13
FROM python:${PYTHON_VERSION}-slim AS base

ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

# Create non-privileged user
ARG UID=10001
RUN adduser \
    --disabled-password \
    --gecos "" \
    --home "/app" \
    --shell "/sbin/nologin" \
    --uid "${UID}" \
    appuser

# Install build dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    python3-dev \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency specification
COPY pyproject.toml ./

# Install dependencies from pyproject.toml
RUN pip install --no-cache-dir .

# Copy application files
# Cache bust: v20251111-form-flow
COPY src/agent.py ./
COPY config/ ./config/

# Set ownership
RUN chown -R appuser:appuser /app

USER appuser

# Pre-download models
RUN python agent.py download-files

# Run hybrid outbound agent
CMD ["python", "agent.py", "start"]
