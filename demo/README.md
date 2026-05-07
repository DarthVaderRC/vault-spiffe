# HashiBank Vault SPIFFE demo

This demo implements the approved `spec.md` using two Vault Enterprise 2.0 nodes:

- **`hashibank-identity`**: AppRole, PKI, SPIFFE JWT minting, identity templating
- **`hashibank-access`**: SPIFFE auth, dynamic Postgres credentials, banking access policies

It also includes:

- **`postgres-hashibank`** with seeded `fraud_alerts` data
- **`hashibank-fraud-web`** for the JWT + dynamic DB credentials flow
- **`hashibank-assistant`** for the OIDC/JWKS validation flow
- **`demo-tools`** for the X.509 payments script and ad hoc inspection

## What the demo proves

1. **Payments API X.509 flow**
   - AppRole on `hashibank-identity`
   - PKI-issued certificate with `spiffe://hashibank.demo/payments/api`
   - SPIFFE X.509 auth on `hashibank-access`
   - Read of a payments-scoped proof secret

2. **Fraud Ops JWT flow**
   - AppRole on `hashibank-identity`
   - SPIFFE JWT-SVID minted from alias metadata
   - SPIFFE JWT auth on `hashibank-access`
   - Dynamic Postgres credentials from Vault
   - Query of `fraud_alerts` and render on a dummy page

3. **Relationship assistant OIDC flow**
   - AppRole on `hashibank-identity`
   - SPIFFE JWT-SVID minting
   - Validation through `.well-known/openid-configuration` and JWKS
   - Render of masked banking context

## Prerequisites

- Docker Desktop or Docker Engine with Compose v2
- The Vault Enterprise license file already placed at `../license.hclic`

The Compose file defaults to:

```text
hashicorp/vault-enterprise:2.0.0-ent
```

Override with an environment variable if needed:

```bash
export VAULT_ENTERPRISE_IMAGE=hashicorp/vault-enterprise:2.0-ent
```

The demo also uses host ports that are less likely to collide with an existing local Vault:

```text
hashibank-identity  -> https://localhost:18200
hashibank-access    -> https://localhost:18300
fraud web           -> http://localhost:18081
assistant web       -> http://localhost:18082
```

All four can be overridden with:

```bash
export HASHIBANK_IDENTITY_HOST_PORT=18200
export HASHIBANK_ACCESS_HOST_PORT=18300
export HASHIBANK_FRAUD_WEB_PORT=18081
export HASHIBANK_ASSISTANT_WEB_PORT=18082
```

## Bootstrapping

From the `demo/` directory:

```bash
./scripts/bootstrap.sh
```

The bootstrap script will:

1. Generate local TLS assets under `demo/config/tls/`
2. Start `hashibank-identity`, `hashibank-access`, `postgres-hashibank`, and `demo-tools`
3. Initialize and unseal both Vault nodes
4. Configure AppRole, PKI, SPIFFE, database secrets, policies, and demo personas
5. Start the two demo web apps

## Tear down and reset

From the `demo/` directory:

```bash
./scripts/teardown.sh
```

This:

1. Stops the Compose stack
2. Removes containers, named volumes, and orphaned services
3. Removes generated local runtime artifacts, including root tokens, AppRole secrets, generated certificates, and local TLS material

After teardown, rerun:

```bash
./scripts/bootstrap.sh
```

## Running the demo flows

### Payments API X.509

```bash
./scripts/demo-x509-payments.sh
```

The script prints JSON showing:

- the SPIFFE URI SAN on the issued certificate
- the payments-scoped auth result from `hashibank-access`
- the proof secret read with the returned Vault token

### Fraud Ops JWT + database credentials

```bash
./scripts/demo-jwt-fraud.sh
```

This prints the raw JSON from the app route:

```text
http://localhost:18081/api/demo
```

Open the human-readable page at:

```text
http://localhost:18081/
```

### Relationship assistant OIDC validation

```bash
./scripts/demo-agentic-oidc.sh
```

This prints the raw JSON from the app route:

```text
http://localhost:18082/api/demo
```

Open the human-readable page at:

```text
http://localhost:18082/
```

## Runtime artifacts

Bootstrap writes ephemeral material under `demo/runtime/`, including:

- Vault init outputs and root tokens
- generated AppRole role IDs and secret IDs
- rendered SPIFFE template files
- generated payments certificate and key

`demo/runtime/` is intentionally git-ignored.

Generated TLS material under `demo/config/tls/` is also treated as ephemeral local output and is removed by `./scripts/teardown.sh`.

## Demo notes

- The X.509 flow uses **Vault PKI** with a SPIFFE URI SAN. It is not claiming native X.509 SVID issuance from the SPIFFE secrets engine.
- The JWT flow uses **Vault SPIFFE secrets** for JWT-SVID minting and **Vault SPIFFE auth** for relying-party authentication.
- The SCIM and SAML parts remain content-first in the manuscript and are not runnable flows in this first implementation pass.

## Troubleshooting

- If a local port is already in use, override the host port environment variables described above before running `./scripts/bootstrap.sh`.
- If the pinned Enterprise image tag does not start with your license because of build-date/license compatibility, set `VAULT_ENTERPRISE_IMAGE` to another compatible 2.0 enterprise tag and rerun bootstrap.
