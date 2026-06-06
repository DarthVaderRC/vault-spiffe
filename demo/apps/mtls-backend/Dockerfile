FROM python:3.12-slim

COPY apps/mtls-backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY apps/mtls-backend /workspace/app

WORKDIR /workspace/app

CMD ["python", "app.py"]
