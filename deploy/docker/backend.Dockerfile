# C-ECM backend — production image.
#
# Linux only: the FileNet native-write path (routers -> a local WebSphere
# Java runtime via subprocess/EJB-IIOP) needs a real Windows + WebSphere
# install and can't be containerized here — see deploy/windows/ for that
# path instead. Everything else (all 53 other providers, all of C-ECM's own
# features) works identically in this image.
#
# Build from the REPO ROOT so both `backend/` and `deploy/` are in context:
#   docker build -f deploy/docker/backend.Dockerfile -t cecm-backend .
FROM python:3.12-slim

# libxmlsec1-dev/pkg-config/gcc: python3-saml's xmlsec dependency has no
# reliable manylinux wheel and builds from source against these on Linux
# (unlike Windows, where a prebuilt wheel exists) -- without them pip
# install fails partway through, not at container start.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libxmlsec1-dev libxmlsec1-openssl pkg-config gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
COPY deploy/docker/requirements-prod.txt ./requirements-prod.txt
RUN pip install --no-cache-dir -r requirements.txt -r requirements-prod.txt

COPY backend/app ./app
COPY backend/run.py ./run.py

# Owned by an unprivileged user -- DATA_DIR (mounted as a volume in
# docker-compose.yml) must be writable by it too.
RUN useradd --create-home --uid 1000 cecm \
    && mkdir -p /app/data && chown -R cecm:cecm /app
USER cecm

EXPOSE 8020
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8020/health', timeout=3)" || exit 1

CMD ["python", "run.py"]
