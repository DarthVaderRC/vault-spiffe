ARG VAULT_CLI_IMAGE=hashicorp/vault-enterprise:2.0.0-ent
FROM ${VAULT_CLI_IMAGE} AS vault-cli

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    curl \
    jq \
    openssl \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

COPY --from=vault-cli /bin/vault /usr/local/bin/vault

COPY tools/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /workspace/demo
