ARG VAULT_CLI_IMAGE=hashicorp/vault-enterprise:2.0.0-ent
ARG SPIRE_SERVER_IMAGE=ghcr.io/spiffe/spire-server:1.12.5
ARG SPIRE_AGENT_IMAGE=ghcr.io/spiffe/spire-agent:1.12.5
FROM ${VAULT_CLI_IMAGE} AS vault-cli
FROM ${SPIRE_SERVER_IMAGE} AS spire-server-cli
FROM ${SPIRE_AGENT_IMAGE} AS spire-agent-cli

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    curl \
    jq \
    openssl \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

COPY --from=vault-cli /bin/vault /usr/local/bin/vault
COPY --from=spire-server-cli /opt/spire/bin/spire-server /usr/local/bin/spire-server
COPY --from=spire-agent-cli /opt/spire/bin/spire-agent /usr/local/bin/spire-agent

COPY tools/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /workspace/demo
