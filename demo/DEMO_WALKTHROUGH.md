# SPIFFE Secrets Engine and SPIFFE Auth Method Demonstration

Use this guide during the live HashiBank Vault + SPIFFE demo. It gives you the operator steps, the use cases, and the field-level highlights to call out while the commands run.

## Setup

1. Start from a clean state when you want a predictable run:

   ```bash
   cd demo
   ./scripts/teardown.sh
   ./scripts/bootstrap.sh
   ```

2. Keep one terminal open in `demo/` for commands.
3. Keep two browser tabs ready:
   - `http://localhost:18081/`
   - `http://localhost:18082/`
4. Before the first scenario, review the setup live:

   ```bash
   ./scripts/bootstrap.sh review
   ```

The review is split into logical sections and pauses after each one.

Review output shows:

   - AppRole definitions and alias metadata
   - policies being written
   - PKI role for payments certificates
   - SPIFFE engine configuration and role definitions
   - SPIFFE auth configuration and role definitions
   - payments API KV secrets path

## Demo order

Run the scenarios in this order:

1. **Payments API X.509** shows standards-based X.509 identity and policy mapping.
2. **Fraud Ops JWT-SVID** shows short-lived identity turning into live banking data.
3. **Relationship assistant OIDC** shows the same identity model working with a downstream service that validates JWTs through discovery and JWKS.

## Explain the demo architecture

Use this before the first scenario:

- This demo runs on one HashiBank Vault Enterprise Cluster.
- The cluster is already unsealed and ready to use.
- Secrets engines are components which store, generate and encrypt data.
- Auth methods are components which verify the identity and assign policies for accessing secrets
- In the context of SPIFFE, the same cluster issues SPIFFE IDs, validates the SVIDs, and maps that identity to policies via Vault roles.
- The trust domain is `hashibank.demo` environment

*I have built a shell wrapper over that runs the commands necessary to demonstrate the flows.*

## Scenario 1: Payments API with X.509 SPIFFE auth

### Business context

HashiBank runs an internal payments API workload that moves funds between banking systems. The bank wants a standards-based machine identity instead of long-lived credentials or a certificate reused by multiple services.

### Steps

1. Run the scenario script:

   ```bash
   ./scripts/demo-x509-payments.sh
   ```
2. Key call outs:
   - AppRole:
      - AppRole role definition
      - `auth.alias_metadata`
      - `auth.token_policies`
   - PKI issue:
      - PKI role definition
      - `openssl x509 -text` output showing URI SAN value
         ```text
         spiffe://hashibank.demo/payments/api
         ```
   - SPIFFE X.509 auth:
      - SPIFFE X.509 auth role definition
      - policy mapping result

   - Final KV read secrets

### Explanation

- The payments API authenticates to Vault with an approle.
- Vault PKI issues a certificate that carries the SPIFFE URI SAN for `payments-api`.
- The same Vault cluster then accepts that certificate through the SPIFFE X.509 auth mount and maps it to a payments policy.
- The outcome is not just successful auth. The outcome is access to payments API KV secrets without a static shared token.

### Key takeaway

- Vault can issue X.509 credentials with SPIFFE naming.
- Vault can use SPIFFE X.509 auth to map that identity to a narrow fine-grained policy.
- This is useful when you want X.509-based workload identity.

## Scenario 2: Fraud Ops JWT-SVID to dynamic Postgres credentials

### Business context

HashiBank runs a fraud operations dashboard that needs to read flagged transaction data. The bank does not want the application to keep a static database password. It wants a short-lived workload identity to become short-lived database access.

### Steps

1. Run the scenario script:

   ```bash
   ./scripts/demo-jwt-fraud.sh
   ```

2. Key call outs:
   - AppRole:
      - AppRole role definition
      - `auth.alias_metadata`
      - `auth.token_policies`
   - JWT mint:
      - SPIFFE role definition
      - decoded JWT claims
      - `sub`
         ```text
         spiffe://hashibank.demo/fraud/ops-web
         ```
      - `vault.entity.id`
   - SPIFFE JWT auth:
      - SPIFFE JWT auth role definition
      - `auth.display_name`
      - policy mapping result
   - Dynamic DB credentials:
      - `db_username`
      - `lease_id`
      - `lease_duration`
   - Final reveal:
      - SQL-backed result set
      - refresh `http://localhost:18081/`

### Explanation

- Here the same Vault cluster mints the JWT-SVID and then accepts it back through SPIFFE JWT auth.
- That authenticated Vault token reads dynamic Postgres credentials.
- The fraud dashboard uses those short-lived credentials to read real fraud alert rows.
- This is the most practical business proof in the demo: identity becomes data access, not just a token exchange.

### Key takeaway

- Vault can mint a standards-aligned JWT workload identity from its internal identity graph.
- SPIFFE auth can exchange that identity for a policy-scoped Vault token.
- Vault can turn that token into short-lived database credentials tied to a concrete banking use case.

## Scenario 3: Relationship assistant with OIDC validation

### Business context

HashiBank wants an internal banker assistant that can carry portable workload identity across system boundaries. The assistant service should be able to validate the workload JWT through discovery and JWKS without depending on Vault-native auth semantics.

### Steps

1. Run the scenario script:

   ```bash
   ./scripts/demo-agentic-oidc.sh
   ```

2. Key call outs:
   - AppRole:
      - AppRole role definition
      - `auth.alias_metadata`
      - `auth.token_policies`
   - JWT mint:
      - SPIFFE role definition
      - decoded JWT claims
      - `sub`
      - `aud`
   - Discovery and JWKS:
      - discovery document
      - `jwks_uri`
      - JWKS response
   - JWT validation:
      - validated claims
      - `iss`
      - `aud`
      - `vault.entity.id`
   - Final reveal:
      - masked banker context
      - refresh
         ```text
         http://localhost:18082/
         ```

### Explanation

- This scenario uses the same SPIFFE JWT model, but the consumer is a banker assistant service rather than Vault auth.
- The service resolves discovery and JWKS from the SPIFFE engine and validates the JWT with OIDC-style patterns.
- That keeps the identity model portable across tool boundaries.
- The assistant page is the business-facing proof that the JWT was validated successfully.

### Key takeaway

- Vault-minted SPIFFE JWTs are not limited to Vault auth flows.
- Discovery and JWKS make the JWT usable by downstream services that speak standard OIDC-style validation patterns.
- SPIFFE gives a portable workload identifier that fits real banking use cases.

## Closing..

Use this after the third scenario:

- HashiBank uses Vault as the trust and policy plane, not as a generic token vending machine.
- SPIFFE IDs sit above Vault entities and aliases as the portable workload identifier layer.
- The demo shows three concrete outcomes: payments API policy mapping, fraud data access, and banker assistant validation.

## Reset after the demo

When you finish the session, run:

```bash
./scripts/teardown.sh
```
