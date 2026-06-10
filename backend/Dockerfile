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

# Verify media files are present in the image
RUN echo "=== Media files in /app/media/ ===" && ls -la /app/media/ && echo "=== Videos in /app/videos/ ===" && ls -la /app/videos/

# Download Karing Windows x64 exe from GitHub releases (non-fatal — ~45MB)
RUN apt-get update && apt-get install -y --no-install-recommends curl jq && rm -rf /var/lib/apt/lists/*; \
    if [ ! -s media/karing_windows_x64.exe ]; then \
      KARING_TAG=$(curl -sL https://api.github.com/repos/KaringX/karing/releases/latest \
        | jq -r '.tag_name') && \
      curl -sL "https://github.com/KaringX/karing/releases/download/${KARING_TAG}/karing_${KARING_TAG#v}_windows_x64.exe" \
        -o media/karing_windows_x64.exe || echo "WARNING: Karing exe download failed, Windows step 1 will use GitHub link only"; \
    fi; \
    ls -la media/karing_windows_x64.exe 2>/dev/null || echo "WARNING: karing_windows_x64.exe not found"

COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

ENV PYTHONPATH=/app/src

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
