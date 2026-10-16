# Build from the repository root so the AIAssistant/ prefix resolves:
#   docker build -f AIAssistant/Dockerfile -t ai-assistant .
# The image runs the platform entry point; it does NOT ship model weights
# (mount them at /app/AIAssistant/Models or set APP_DATA_DIR).
#
# Multi-stage (10/10 hardening): the builder compiles/installs wheels, the
# runtime carries only the installed site-packages + source tree, run as a
# non-root user so a tool-execution escape does not inherit uid 0.
FROM python:3.11-slim AS builder

WORKDIR /build

# Dependency set first so source changes do not invalidate the wheel layer.
COPY AIAssistant/requirements.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

COPY AIAssistant ./AIAssistant
# Editable install keeps ``__file__`` inside /app/AIAssistant, so config/ and
# logs/ resolve exactly as they do in a source checkout (a plain install would
# relocate them into site-packages and drop config/*.toml on the floor).
# Installed into the same prefix so the runtime copies one tree.
RUN pip install --no-cache-dir --prefix=/install -e ./AIAssistant


FROM python:3.11-slim AS runtime

WORKDIR /app

COPY --from=builder /install /usr/local
COPY --from=builder /build/AIAssistant ./AIAssistant
# The editable-install finder points back at /build/AIAssistant; re-install
# the pointer against the runtime path (no network, no deps re-resolve).
RUN pip install --no-cache-dir -e ./AIAssistant --no-deps

ENV AGENT_OBSERVABILITY=1
ENV PYTHONUNBUFFERED=1
# Data (models, RAG cache, task store, logs) goes to a mountable volume instead
# of wherever the code happens to live.
ENV APP_DATA_DIR=/app/data
# Loopback-only binding makes the port unreachable from outside the container;
# ADR 0008 still gates the agent/admin/chat surfaces to loopback callers, so
# exposing the interface does not open the mutating APIs.
ENV AGENT_HOST=0.0.0.0
ENV AGENT_PORT=8080
ENV PATH="/usr/local/bin:${PATH}"
ENV PYTHONPATH="/app/AIAssistant/src:${PYTHONPATH}"

# Non-root runtime: own code + data dirs, then drop privileges.
RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/data /app/AIAssistant/logs \
    && chown -R appuser:appuser /app /usr/local/lib/python3.11/site-packages/__editable__* 2>/dev/null || true

EXPOSE 8080

# python:3.11-slim has no curl — probe with the stdlib instead.
# /readyz answers 503 while the LLM is not loaded, 200 when it is, so a
# first-boot model download shows up as "starting" rather than a dead service.
# start-period is generous: a cold index build + model load measured ~337s.
HEALTHCHECK --interval=30s --timeout=5s --start-period=600s --retries=3 \
  CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/readyz', timeout=4).status == 200 else 1)"

USER appuser

CMD ["python", "-m", "ai_assistant.bootstrap.server"]
