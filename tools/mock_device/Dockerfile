FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends openssl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "aiohttp>=3.10,<4"

WORKDIR /opt
COPY . /opt/mock_device
ENV PYTHONPATH=/opt
EXPOSE 443 8080
CMD ["python", "-m", "mock_device"]
