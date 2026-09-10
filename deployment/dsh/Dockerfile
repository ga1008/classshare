# Build from repository root: docker build -f deployment/dsh/Dockerfile .
# Official multi-platform manifest verified 2026-09-10; no floating base tag.
FROM node:24.13.0-bookworm-slim@sha256:4660b1ca8b28d6d1906fd644abe34b2ed81d15434d26d845ef0aced307cf4b6f

ARG DEBIAN_MIRROR=http://deb.debian.org
RUN sed -i "s|http://deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends bash bubblewrap ca-certificates git python3 ripgrep tini \
       python3-docx python3-openpyxl python3-reportlab python3-pil python3-venv python3-xlsxwriter \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 dsh \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin dsh

WORKDIR /opt/lanshare-dsh
COPY deployment/dsh/artifact-requirements.txt ./artifact-requirements.txt
# Bookworm has no python3-pptx package. Its pure Python wheel and typing helper
# are fixed by hash; lxml/Pillow/XlsxWriter come from signed Debian packages.
RUN python3 -m venv --system-site-packages /opt/lanshare-dsh/python \
    && /opt/lanshare-dsh/python/bin/pip install --no-cache-dir --no-deps --require-hashes -r artifact-requirements.txt
COPY deployment/dsh/package.json deployment/dsh/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund \
    && test "$(node -p "require('./node_modules/@deepseek-ai/dsh/package.json').version")" = '0.1.5-rc.1' \
    && npm cache clean --force

COPY deployment/dsh/profile ./profile
COPY deployment/dsh/entrypoint.py ./entrypoint.py
COPY deployment/dsh/runner.mjs ./runner.mjs
COPY deployment/dsh/gateway-relay.mjs ./gateway-relay.mjs
COPY deployment/dsh/skills ./skills
COPY deployment/dsh/plugins ./plugins
RUN python3 /opt/lanshare-dsh/entrypoint.py --write-profile-digest \
    && mkdir -p /opt/lanshare-dsh/profile/node_modules /opt/lanshare-dsh/profile/.dsh-module-fallback/node_modules \
    && touch /opt/lanshare-dsh/profile/cordis.yml \
    && chmod -R a-w /opt/lanshare-dsh \
    && mkdir -p /workspace /var/lib/dsh \
    && chown 10001:10001 /workspace /var/lib/dsh

USER 10001:10001
WORKDIR /workspace
ENV DSH_HOME=/var/lib/dsh
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/usr/bin/python3", "/opt/lanshare-dsh/entrypoint.py"]
