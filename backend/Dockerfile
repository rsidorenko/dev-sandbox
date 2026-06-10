FROM python:3.12-slim AS builder

WORKDIR /build

COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /install /usr/local

# Source must be on PYTHONPATH so that default_migrations_directory()
# resolves parents[3] to /app/ correctly (not site-packages).
COPY src/ src/
COPY videos/ videos/
COPY media/ media/
COPY migrations/ migrations/

# Download Karing Windows x64 exe from GitHub releases (not stored in git — ~45MB)
RUN apt-get update && apt-get install -y --no-install-recommends curl jq && rm -rf /var/lib/apt/lists/* && \
    if [ ! -s media/karing_windows_x64.exe ]; then \
      KARING_TAG=$(curl -sL https://api.github.com/repos/KaringX/karing/releases/latest \
        | jq -r '.tag_name') && \
      curl -sL "https://github.com/KaringX/karing/releases/download/${KARING_TAG}/karing_${KARING_TAG#v}_windows_x64.exe" \
        -o media/karing_windows_x64.exe; \
    fi
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

ENV PYTHONPATH=/app/src

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
