FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

COPY apps/hashibank-fraud-web/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY python /workspace/python
COPY apps/hashibank-fraud-web /workspace/app

ENV PYTHONPATH=/workspace/python
WORKDIR /workspace/app

CMD ["python", "app.py"]
