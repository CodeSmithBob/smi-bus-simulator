# SMI bus simulator: web UI on 8080, one raw TCP port per SMI line from 4000.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install . \
 && useradd --create-home --uid 10001 smisim \
 && usermod -aG dialout smisim

USER smisim
EXPOSE 8080 4000-4015

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8080/api/meta', timeout=2)" || exit 1

ENTRYPOINT ["smisim"]
CMD ["run", "--buses", "1", "--motors", "16"]
