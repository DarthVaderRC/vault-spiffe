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
4. If you plan to show the SPIRE extension, bootstrap it before the SPIRE scenarios:

   ```bash
   ./scripts/bootstrap-spire.sh
   ```
5. Before the first scenario, review the setup live:

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
4. **SPIRE JWT-SVID to Vault auth** shows Vault accepting a SPIRE-issued JWT-SVID through a dedicated auth mount.
5. **Vault as SPIRE upstream authority** shows SPIRE workload certificates chaining back to a Vault-managed root.

## Explain the demo architecture

Use this before the first scenario:

- This demo runs on one HashiBank Vault Enterprise Cluster.
- The cluster is already unsealed and ready to use.
- Secrets engines are components which store, generate and encrypt data.
- Auth methods are components which verify the identity and assign policies for accessing secrets
- In the context of SPIFFE, the same cluster issues SPIFFE IDs, validates the SVIDs, and maps that identity to policies via Vault roles.
- The trust domain is `hashibank.demo` environment
- The optional SPIRE overlay uses a separate trust domain, `spire.hashibank.demo`, to avoid pretending Vault-native and SPIRE-issued identities share one bundle.
- In the SPIRE overlay, Vault consumes the SPIRE federation bundle for **JWT-SVID** auth and acts as the upstream authority for **SPIRE server X.509 CA** material.
- We are **not** claiming a shipped SPIRE X.509-SVID -> Vault auth path in this repo because the clean bundle/root-trust model still did not authenticate successfully in this lab.
- The only working X.509 workaround was trusting the SPIRE issuing intermediate directly, which we are deliberately not presenting as the supported model.

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

## Scenario 4: SPIRE JWT-SVID -> Vault SPIFFE auth

### Business context

HashiBank wants to show the line between Vault-native SPIFFE and SPIRE-issued workload identity. A platform team already runs SPIRE for workload issuance and wants Vault to accept that identity without teaching the workload a second auth scheme.

### Steps

1. Run the scenario script:

   ```bash
   ./scripts/demo-spire-jwt.sh
   ```

2. Key call outs:
   - SPIRE fetch:
      - `spire-agent api fetch jwt`
      - decoded claims
      - `sub`
         ```text
         spiffe://spire.hashibank.demo/workloads/vault-spire-client
         ```
      - `aud`
         ```text
         vault-spire-demo
         ```
   - Vault trust configuration:
      - `auth/spire-jwt/config`
      - `endpoint_url`
         ```text
         https://spire-server:8443
         ```
      - `trust_domain`
         ```text
         spire.hashibank.demo
         ```
   - Vault auth result:
      - `auth/spire-jwt/role/vault-spire-client`
      - policy mapping result
   - Final proof:
      - `vault kv get kv/spire/demo`

### Explanation

- The workload identity comes from SPIRE, not from Vault.
- Vault trusts the SPIRE federation bundle on `auth/spire-jwt/`.
- The workload presents its SPIRE-issued JWT-SVID to Vault through the SPIFFE auth method and receives a Vault token scoped by `workload_id_patterns`.
- The KV read is the business proof that the identity exchange became authorization, not just token inspection.

### Key takeaway

- Vault can accept a SPIRE-issued JWT-SVID through documented SPIFFE federation bundle integration.
- This is the cleanest supported SPIRE -> Vault auth path in the local demo.

## Scenario 5: Vault as SPIRE upstream authority

### Business context

HashiBank also wants to show that Vault can stay the root of trust even when SPIRE handles workload issuance. The question is not "does Vault replace SPIRE?" The question is "can Vault remain the enterprise trust anchor while SPIRE issues the workload certificates?"

### Steps

1. Run the scenario script:

   ```bash
   ./scripts/demo-spire-upstreamauthority.sh
   ```

2. Key call outs:
   - Vault root:
      - `vault read spire-pki/cert/ca`
      - root certificate subject
   - SPIRE workload certificate:
      - `spire-agent api fetch x509`
      - leaf SPIFFE ID
      - issuing intermediate subject
   - Final proof:
      - `openssl verify -CAfile runtime/spire/agent/bootstrap/bootstrap-trust-bundle.pem -untrusted runtime/generated/spire-upstreamauthority/intermediate.pem runtime/generated/spire-upstreamauthority/leaf.pem`

### Explanation

- `upstreamauthority_vault` lets SPIRE server obtain its X.509 CA material from Vault PKI.
- The workload still gets its SVID from SPIRE agent and SPIRE server.
- The chain validation shows that the SPIRE workload certificate roots back to the certificate stored in Vault `spire-pki/`.

### Key takeaway

- Vault can act as the upstream X.509 trust anchor for SPIRE without pretending to be the workload attestor.
- This is an X.509 CA delegation story, not a JWT signing-key publication story.

## Closing..

Use this after the final scenario:

- HashiBank uses Vault as the trust and policy plane, not as a generic token vending machine.
- SPIFFE IDs sit above Vault entities and aliases as the portable workload identifier layer.
- The base demo shows three Vault-native outcomes: payments API policy mapping, fraud data access, and banker assistant validation.
- The SPIRE overlay adds two integration outcomes: Vault accepting a SPIRE JWT-SVID and Vault acting as SPIRE's upstream X.509 root.
- The important boundary is still the same: Vault and SPIRE are complementary when you need attestation and external workload issuance.

## Reset after the demo

When you finish the session, run:

```bash
./scripts/teardown.sh
```
