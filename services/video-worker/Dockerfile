FROM python:3.12.5-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/opt/dogsense-worker

RUN groupadd --system dogsense \
    && useradd --system --gid dogsense --home-dir /nonexistent --shell /usr/sbin/nologin dogsense

WORKDIR /opt/dogsense-worker

COPY requirements.txt requirements-dev.txt ./
RUN pip install --requirement requirements-dev.txt

COPY app ./app
COPY prompts ./prompts
COPY tests ./tests
COPY pyproject.toml ./pyproject.toml
COPY --from=contracts --chown=dogsense:dogsense . /packages/contracts

USER dogsense
EXPOSE 8081

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081/health/live', timeout=2)"]

CMD ["python", "-m", "app.main"]
