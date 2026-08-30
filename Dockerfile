FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN set -eux; \
    sed -i \
      -e 's|http://deb.debian.org|http://mirrors.cloud.tencent.com|g' \
      -e 's|https://deb.debian.org|http://mirrors.cloud.tencent.com|g' \
      -e 's|http://security.debian.org|http://mirrors.cloud.tencent.com|g' \
      -e 's|https://security.debian.org|http://mirrors.cloud.tencent.com|g' \
      /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
    apt-get update -o Acquire::Retries=3 -o Acquire::http::Timeout=30; \
    apt-get install -y --no-install-recommends git openssh-client ca-certificates; \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -i https://mirrors.cloud.tencent.com/pypi/simple --timeout 120 -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
