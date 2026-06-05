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
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

ENV PYTHONPATH=/app/src

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
