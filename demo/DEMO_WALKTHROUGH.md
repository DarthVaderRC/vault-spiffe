# Vault as Control Plane for SPIFFE Identities

Use this guide during the live HashiBank Vault + SPIFFE demo. It gives you the operator steps, the customer story, and the field-level highlights to call out while the commands run.

## Setup

1. Start from a clean state when you want a predictable run:

   ```bash
   cd demo
   ./scripts/teardown.sh
   ./scripts/bootstrap.sh
   ```

2. Keep one terminal open in `demo/` for commands.
3. Confirm the repository root contains `license.hclic`; the default lab path uses Vault Enterprise features.
4. If you plan to show the SPIRE extension, bootstrap it before the SPIRE scenarios:

   ```bash
   ./scripts/bootstrap.sh spire
   ```

5. Before the first scenario, review the setup live:

   ```bash
   ./scripts/bootstrap.sh review
   ```

The review is split into logical sections and pauses after each one.

Review output shows:

   - Vault Kubernetes auth configuration
   - bound roles for the Kubernetes workloads
   - PKI and SPIFFE issuer roles
   - demo namespace service accounts, pods, and services

## Demo order

Run the scenarios in this order:

1. **Zero-trust mTLS between Kubernetes microservices** shows Vault-issued certificate identity being authorized outside Vault on both peers.
2. **Cross-network API authentication using JWT-SVID** shows a workload using Vault-minted JWT identity outside Vault.
3. **SPIRE JWT-SVID to Vault auth and DB brokering** shows Vault accepting a SPIRE-issued JWT-SVID and turning it into short-lived database access.
4. **Vault as SPIRE upstream authority** shows SPIRE workload certificates chaining back to a Vault-managed root.

## Explain the demo architecture

Use this before the first scenario:

- This demo uses one HashiBank Vault Enterprise cluster as the trust and authorization control plane.
- The recommended path adds a local `kind` cluster so Vault Kubernetes auth works with a real Kubernetes API and real service account tokens.
- The Vault-native story now starts with Kubernetes identity, not AppRole loops.
- Vault uses the service account metadata from Kubernetes auth to derive SPIFFE-aligned subjects.
- For X.509, Vault PKI issues a certificate with a SPIFFE URI SAN and the workload uses it over mTLS.
- For JWT, Vault mints a JWT-SVID and a downstream service validates it through discovery and JWKS.
- The lab advertises `vault.demo.internal` as the canonical issuer, while pods reach the host-side Vault cluster through `host.docker.internal`.
- The trust domain for the Vault-native flows is `hashibank.demo`.
- The optional SPIRE overlay uses a separate trust domain, `spire.hashibank.demo`, to avoid pretending Vault-native and SPIRE-issued identities share one bundle.
- In the SPIRE overlay, Vault consumes the SPIRE federation bundle for **JWT-SVID** auth and acts as the upstream authority for **SPIRE server X.509 CA** material.
- We are **not** claiming a shipped SPIRE X.509-SVID -> Vault auth path in this repo because the clean bundle/root-trust model still did not authenticate successfully in this lab.

## Scenario 1: Zero-trust mTLS between Kubernetes microservices

### Business context

HashiBank runs a `payments-api` workload in Kubernetes and wants true zero-trust service-to-service authentication with the settlement-status backend behind `mtls-backend`. Both services should start from Kubernetes identity, receive short-lived Vault-issued certificates, and verify each other's SPIFFE IDs before any payment data is returned.

### Steps

1. Run the scenario script:

   ```bash
   ./scripts/demo-k8s-mtls.sh
   ```

2. Key call outs:
   - Kubernetes auth:
      - `auth/kubernetes/role/payments-api`
      - `auth/kubernetes/role/mtls-backend`
      - bound namespace and service account for both peers
      - login response metadata for the caller:
         - `service_account_name`
         - `service_account_namespace`
   - PKI issue on both sides:
      - `pki/roles/payments-k8s-spiffe`
      - `pki/roles/mtls-backend-k8s-spiffe`
      - `openssl x509 -text` output showing URI SAN values
         ```text
         spiffe://hashibank.demo/ns/payments/sa/payments-api
         ```
         ```text
         spiffe://hashibank.demo/ns/payments/sa/mtls-backend
         ```
   - mTLS business proof:
      - request from `payments-api` to `mtls-backend`
      - client-side verification of the backend SPIFFE ID
      - server-side authorization of the caller SPIFFE ID
      - protected payment settlement status response

### Explanation

- The `payments-api` pod authenticates to Vault with its projected Kubernetes service account token.
- `mtls-backend` does the same, so both sides of the exchange receive short-lived certificates from Vault PKI.
- Vault validates those tokens through the Kubernetes API and returns short-lived issuer tokens tied to the two service accounts.
- Each certificate carries a SPIFFE URI SAN that maps cleanly to the Kubernetes workload identity.
- `payments-api` uses its certificate to start the mTLS session and explicitly checks that the backend presented `spiffe://hashibank.demo/ns/payments/sa/mtls-backend`.
- `mtls-backend` authorizes only the expected caller SPIFFE ID before returning payment status data.

### Key takeaway

- Vault can issue X.509 workload identity with SPIFFE-compatible naming from a real Kubernetes trust source for **both** microservices in the path.
- The proof point is a zero-trust mTLS business transaction, not another Vault auth hop.

## Scenario 2: Cross-network API authentication using JWT-SVID

### Business context

HashiBank runs a `relationship-assistant` workload in Kubernetes and wants a portable JWT identity that can cross service boundaries without teaching every consumer Vault-native auth semantics. The relying party in this scenario is a relationship insights API that should validate the JWT-SVID, enforce business claims, and return a useful banker action.

### Steps

1. Run the scenario script:

   ```bash
   ./scripts/demo-k8s-jwt.sh
   ```

2. Key call outs:
   - Kubernetes auth:
      - `auth/kubernetes/role/relationship-assistant`
      - service account metadata returned at login
   - JWT mint:
      - `spiffe/role/relationship-assistant-k8s`
      - decoded JWT claims
      - `sub`
         ```text
         spiffe://hashibank.demo/ns/assistants/sa/relationship-assistant
         ```
      - `aud`
         ```text
         relationship-insights-api
         ```
      - business metadata:
         - `bank`
         - `application`
         - `line_of_business`
         - `customer_data_domain`
   - Discovery and JWKS:
      - discovery document
      - `jwks_uri`
      - JWKS response
   - Downstream authorization proof:
      - protected response from the relationship insights API
      - validated claims:
         - `iss`
         - `aud`
         - `sub`
      - masked relationship insights
      - next-best action in the assistant UI on `http://localhost:18082/`

### Explanation

- The `relationship-assistant` pod authenticates to Vault with Kubernetes auth and receives a short-lived issuer token.
- Vault mints a JWT-SVID whose subject is derived from the Kubernetes identity metadata.
- Vault also adds business context claims so the relying party sees both workload identity and application context.
- The demo explicitly shows the SPIFFE engine discovery document and JWKS output.
- The workload then presents the JWT-SVID to `jwt-consumer`, which validates the token through those public endpoints, authorizes the required subject and business claims, and returns masked relationship insights plus the next-best action.

### Key takeaway

- Vault-minted JWT-SVIDs are useful outside Vault when the consumer can validate them through discovery and JWKS **and** authorize business context, not just signature details.
- The customer can see the complete chain: Kubernetes identity -> JWT-SVID -> downstream API authorization -> banker action.

## Scenario 3: SPIRE JWT-SVID -> Vault auth -> dynamic DB access

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
   - Database proof:
      - `database/creds/fraud-readonly`
      - queried rows from `fraud_alerts`
      - rendered `hashibank-fraud-web` page on `http://localhost:18081/`

### Explanation

- The workload identity comes from SPIRE, not from Vault.
- Vault trusts the SPIRE federation bundle on `auth/spire-jwt/`.
- The workload presents its SPIRE-issued JWT-SVID to Vault through the SPIFFE auth method and receives a Vault token scoped by `workload_id_patterns`.
- Vault then brokers a short-lived Postgres login and the workload uses it to query the fraud-alerts table.
- The fraud dashboard proves the result is business data access, not just token inspection.

### Key takeaway

- Vault can accept a SPIRE-issued JWT-SVID through documented SPIFFE federation bundle integration.
- This is the cleanest supported SPIRE -> Vault auth path in the local demo.

## Scenario 4: Vault as SPIRE upstream authority

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

## Closing

Use this after the final scenario:

- HashiBank uses Vault as the trust and policy plane, not as a generic token vending machine.
- In the default path, Kubernetes service accounts are the initial trust source and SPIFFE IDs are the portable workload identifier.
- The Vault-native story now shows two realistic outcomes outside Vault: mTLS and downstream JWT verification.
- The SPIRE overlay adds two integration outcomes: Vault accepting a SPIRE JWT-SVID and Vault acting as SPIRE's upstream X.509 root.
- The architectural boundary stays the same: Vault and SPIRE are complementary when you need workload attestation and enterprise trust anchoring together.

## Reset after the demo

When you finish the session, run:

```bash
./scripts/teardown.sh
```
