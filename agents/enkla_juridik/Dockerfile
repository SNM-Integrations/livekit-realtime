# Dockerfile for Enkla Juridik Agent
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
COPY agent.py ./
# Note: For lk agent deploy, prompts are copied from project root
# For manual docker builds, ensure Prompts/ is in the build context
COPY --chown=appuser:appuser Prompts/enkla_juridik_agent_prompt.md ./Prompts/

# Set ownership
RUN chown -R appuser:appuser /app

USER appuser

# Pre-download models
RUN python agent.py download-files

# Run Enkla Juridik agent
CMD ["python", "agent.py", "start"]
