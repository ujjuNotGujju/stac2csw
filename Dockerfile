# Versions pinned to what the test-suite was run with (Python 3.12, pycsw 2.6.2).
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install ".[server]"
COPY config ./config
COPY scripts ./scripts
COPY tests/fixtures ./tests/fixtures
ENV PYCSW_CONFIG=/app/config/pycsw.cfg PYCSW_ROOT=/app
EXPOSE 8000
CMD ["gunicorn", "pycsw.wsgi:application", "-b", "0.0.0.0:8000", "-w", "2", "--log-level", "warning"]
