FROM python:3.13-slim

# Keep this image minimal but include everything the simulator needs at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends openssl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir aiohttp==3.12.15
