# HashiBank demo walkthrough and talk track

Use this guide during the live HashiBank Vault + SPIFFE demo. It gives you the operator steps, the banking use case, and the field-level highlights to call out while the commands run.

## Demo goal

Use the three scenarios to make one architectural point:

- Vault acts as the trust, identity-context, and policy control plane.
- SPIFFE IDs act as the portable workload identifier layer.
- Short-lived identity should lead to a real banking outcome, not stop at authentication.

## Recommended setup

1. Start from a clean state when you want a predictable run:

   ```bash
   cd demo
   ./scripts/teardown.sh
   ./scripts/bootstrap.sh
   ```

2. Keep one terminal open in `demo/` for the presenter commands.
3. Keep two browser tabs ready:
   - `http://localhost:18081/`
   - `http://localhost:18082/`
4. Before the first scenario, review the setup live:

   ```bash
   ./scripts/bootstrap.sh review
   ```

   The review is split into logical groups and pauses after each section so you can control the pace in the room.

   Use this review output to show:

   - the policies being written
   - the AppRole definitions and alias metadata
   - the PKI role for payments certificates
   - the SPIFFE engine role definitions
   - the SPIFFE auth role definitions
   - the payments API KV secrets path

## Suggested demo order

Run the scenarios in this order:

1. **Payments API X.509** shows standards-based X.509 identity and policy mapping.
2. **Fraud Ops JWT-SVID** shows short-lived identity turning into live banking data.
3. **Relationship assistant OIDC** shows the same identity model working with a downstream service that validates JWTs through discovery and JWKS.

## Opening talk track

Use this before the first scenario:

- "This demo runs on one HashiBank Vault Cluster."
- "The same cluster issues identity material, validates SPIFFE identity, and maps that identity to policy."
- "The trust domain is `hashibank.demo`, and each workload gets a banking-relevant SPIFFE ID."
- "The point is not token plumbing. The point is how workload identity turns into tightly scoped banking access."

## Scenario 1: Payments API X.509 SPIFFE auth

### Business context

HashiBank runs an internal payments API that moves money between banking systems. The bank wants a standards-based machine identity instead of a long-lived Vault token or a certificate reused by multiple services.

### Operator steps

1. Run the checkpoints in order:

   ```bash
   ./scripts/demo-x509-payments.sh approle-login
   ./scripts/demo-x509-payments.sh pki-issue
   ./scripts/demo-x509-payments.sh spiffe-x509-auth
   ./scripts/demo-x509-payments.sh payments-api-kv-secrets
   ```

2. In `approle-login`, call out:
   - the AppRole role definition
   - the raw login response
   - `auth.client_token`
   - `auth.metadata.role_name`

3. In `pki-issue`, call out:
   - the PKI role definition
   - the raw certificate issuance response
   - the raw `payments-api.crt`
   - the `openssl x509 -text` output
   - the URI SAN value:

   ```text
   spiffe://hashibank.demo/payments/api
   ```

4. In `spiffe-x509-auth`, call out:
   - the SPIFFE X.509 auth role definition
   - the raw login response
   - the policy mapping result

5. In `payments-api-kv-secrets`, call out:
   - the raw Vault read response
   - the fact that the KV secrets are unlocked only after the SPIFFE-authenticated login

### Suggested talk track

- "The payments API starts with AppRole because we still need a machine-auth path into Vault."
- "Vault PKI issues a certificate that carries the SPIFFE URI SAN for `payments-api`."
- "The same Vault cluster then accepts that certificate through the SPIFFE X.509 auth mount and maps it to a payments policy."
- "The outcome is not just successful auth. The outcome is access to payments API KV secrets without a static shared token."

### Audience takeaway

- Vault can issue X.509 credentials with SPIFFE naming.
- Vault can use SPIFFE X.509 auth to map that identity to a narrow banking policy outcome.
- This is useful when the customer wants X.509-based workload identity with precise access boundaries.

## Scenario 2: Fraud Ops JWT-SVID to dynamic Postgres credentials

### Business context

HashiBank runs a fraud operations dashboard that needs to read flagged transaction data. The bank does not want the application to keep a static database password. It wants a short-lived workload identity to become short-lived database access.

### Operator steps

1. Run the checkpoints in order:

   ```bash
   ./scripts/demo-jwt-fraud.sh approle-login
   ./scripts/demo-jwt-fraud.sh mint-jwt
   ./scripts/demo-jwt-fraud.sh spiffe-jwt-auth
   ./scripts/demo-jwt-fraud.sh db-creds
   ./scripts/demo-jwt-fraud.sh final-reveal
   ```

2. In `approle-login`, call out:
   - the AppRole alias metadata
   - the raw login response

3. In `mint-jwt`, call out:
   - the SPIFFE role definition
   - the raw mint response
   - the raw JWT-SVID
   - the `sub` value:

   ```text
   spiffe://hashibank.demo/fraud/ops-web
   ```

4. In `spiffe-jwt-auth`, call out:
   - the SPIFFE JWT auth role definition
   - the raw login response
   - the policies in the returned auth block

5. In `db-creds`, call out:
   - the raw database credentials response
   - `db_username`
   - `lease_id`
   - `lease_duration`

6. In `final-reveal`, call out:
   - the SQL-backed result set
   - then refresh `http://localhost:18081/`

### Suggested talk track

- "Here the same Vault cluster mints the JWT-SVID and then accepts it back through SPIFFE JWT auth."
- "That authenticated Vault token reads dynamic Postgres credentials."
- "The fraud dashboard uses those short-lived credentials to read real fraud alert rows."
- "This is the most practical business proof in the demo: identity becomes data access, not just a token exchange."

### Audience takeaway

- Vault can mint a standards-aligned JWT workload identity from its internal identity graph.
- SPIFFE auth can exchange that identity for a policy-scoped Vault token.
- Vault can turn that token into short-lived database credentials tied to a concrete banking use case.

## Scenario 3: Relationship assistant with OIDC validation

### Business context

HashiBank wants an internal banker assistant that can carry portable workload identity across system boundaries. The assistant service should be able to validate the workload JWT through discovery and JWKS without depending on Vault-native auth semantics.

### Operator steps

1. Run the checkpoints in order:

   ```bash
   ./scripts/demo-agentic-oidc.sh approle-login
   ./scripts/demo-agentic-oidc.sh mint-jwt
   ./scripts/demo-agentic-oidc.sh fetch-discovery
   ./scripts/demo-agentic-oidc.sh validate-jwt
   ./scripts/demo-agentic-oidc.sh final-reveal
   ```

2. In `approle-login`, call out:
   - the AppRole alias metadata
   - the raw login response

3. In `mint-jwt`, call out:
   - the SPIFFE role definition
   - the raw mint response
   - the raw JWT-SVID

4. In `fetch-discovery`, call out:
   - the discovery document
   - the `jwks_uri`
   - the JWKS response

5. In `validate-jwt`, call out:
   - the validated claims
   - `sub`
   - `iss`
   - `aud`

6. In `final-reveal`, refresh:

   ```text
   http://localhost:18082/
   ```

### Suggested talk track

- "This scenario uses the same SPIFFE JWT model, but the consumer is a banker assistant service rather than Vault auth."
- "The service resolves discovery and JWKS from the SPIFFE engine and validates the JWT with OIDC-style patterns."
- "That keeps the identity model portable across tool boundaries."
- "The assistant page is the business-facing proof that the JWT was validated successfully."

### Audience takeaway

- Vault-minted SPIFFE JWTs are not limited to Vault auth flows.
- Discovery and JWKS make the JWT usable by downstream services that speak standard OIDC-style validation patterns.
- SPIFFE gives the customer a portable workload identifier that fits real banking use cases.

## Closing talk track

Use this after the third scenario:

- "HashiBank uses Vault as the trust and policy plane, not as a generic token vending machine."
- "SPIFFE IDs sit above Vault entities and aliases as the portable workload identifier layer."
- "The demo shows three concrete outcomes: payments API policy mapping, fraud data access, and banker assistant validation."
- "If the customer needs deep workload attestation, pair Vault with an attestation system instead of over-claiming Vault alone."

## Reset after the demo

When you finish the session, run:

```bash
./scripts/teardown.sh
```
