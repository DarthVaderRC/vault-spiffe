# HashiBank demo walkthrough and talk track

Use this guide when you present the HashiBank Vault + SPIFFE demo live. It gives you the operator steps, the business context for each scenario, and a concise talk track you can use while the demo runs.

## Demo goal

Use the three scenarios to make one architectural point:

- Vault acts as the trust, identity-context, and policy control plane.
- SPIFFE IDs act as the portable workload identifier layer.
- Short-lived identity should lead to a business outcome, not stop at authentication.

## Recommended setup

1. Start from a clean state when you want a predictable run:

   ```bash
   cd demo
   ./scripts/teardown.sh
   ./scripts/bootstrap.sh
   ```

2. Keep one terminal open in `demo/` for the demo commands.
3. Keep two browser tabs ready:
   - `http://localhost:18081/`
   - `http://localhost:18082/`
4. Keep the bootstrap output available so you can refer to the Vault and app URLs.

## Suggested demo order

Run the scenarios in this order:

1. **Payments API X.509** shows standards-based X.509 identity and policy mapping.
2. **Fraud Ops JWT-SVID** shows the strongest business outcome: live banking data with dynamic database credentials.
3. **Relationship assistant OIDC** shows that a Vault-minted SPIFFE JWT can work outside Vault-native consumers.

## Opening talk track

Use this short opening before you run the first scenario:

- "This demo uses two Vault Enterprise clusters."
- "`hashibank-identity` mints identity material. `hashibank-access` accepts workload identity and maps it to policy."
- "The trust domain is `hashibank.demo`, and each workload gets a banking-relevant SPIFFE ID."
- "The goal is not to show token plumbing. The goal is to show how standards-based workload identity turns into controlled business access."

## Scenario 1: Payments API X.509 SPIFFE auth

### Business context

HashiBank runs an internal payments service that moves money between systems. The bank wants that service to use a standards-based machine identity instead of a long-lived Vault token or a shared certificate that several services reuse.

### Operator steps

1. In the demo terminal, run:

   ```bash
   ./scripts/demo-x509-payments.sh
   ```

2. In the JSON output, point out these fields:
   - `spiffe_uri_sans`
   - `vault_policies`
   - `payments_proof`
   - `generated_files`

3. Call out the SPIFFE URI SAN value:

   ```text
   spiffe://hashibank.demo/payments/api
   ```

4. If you want to show where the generated certificate lands on disk, point to:

   ```text
   openssl x509 -text -in demo/runtime/generated/payments-api.crt
   demo/runtime/generated/payments-api.crt
   demo/runtime/generated/payments-api.key
   ```

### Suggested talk track

- "The payments workload starts with AppRole on `hashibank-identity`. That is the issuer-side machine auth path."
- "Vault PKI issues an X.509 certificate with the SPIFFE URI SAN for `payments-api`."
- "The second Vault cluster trusts that SPIFFE identity through the SPIFFE X.509 auth path."
- "The important outcome is policy mapping. The workload gets payments-scoped access without a static shared token."

### What the audience should take away

- Vault can issue X.509 credentials that carry SPIFFE naming.
- A relying-party Vault cluster can accept that identity and map it to a narrow policy outcome.
- The flow is useful when the customer wants X.509-based workload identity with precise access boundaries.

### Transition line

Use this line before the next scenario:

- "The first scenario proves identity-to-policy mapping. The next scenario proves that the same model can unlock a real banking outcome after authentication."

## Scenario 2: Fraud Ops JWT-SVID to dynamic Postgres credentials

### Business context

HashiBank runs a fraud dashboard that needs to read flagged transaction data. The bank does not want the application to keep a static database password. It wants the workload to authenticate with short-lived identity, get short-lived database credentials, and read only the rows it needs.

### Operator steps

1. In the demo terminal, run:

   ```bash
   ./scripts/demo-jwt-fraud.sh
   ```

2. In the JSON output, point out these fields:
   - `spiffe_subject`
   - `vault_policies`
   - `db_username`
   - `db_lease_id`
   - `db_lease_duration`

3. Call out the SPIFFE subject value:

   ```text
   spiffe://hashibank.demo/fraud/ops-web
   ```

4. Open the browser page:

   ```text
   http://localhost:18081/
   ```

5. Point out the rendered fields on the page:
   - SPIFFE subject
   - dynamic database username
   - lease information
   - flagged transaction rows from `fraud_alerts`

### Suggested talk track

- "This workload authenticates to `hashibank-identity` with AppRole and mints a SPIFFE JWT-SVID."
- "It presents that JWT to `hashibank-access`, which returns a Vault token based on the workload identity."
- "That Vault token reads dynamic Postgres credentials from the database secrets engine."
- "The app uses those short-lived credentials to query the `fraud_alerts` table and render live banking data."
- "This is the business-value scenario. Authentication is the bridge to controlled data access, not the end state."

### What the audience should take away

- Vault can mint a standards-aligned JWT workload identity from its internal identity graph.
- SPIFFE auth can exchange that identity for policy-scoped Vault access.
- Vault can convert that access into short-lived database credentials tied to a real business function.

### Transition line

Use this line before the next scenario:

- "The fraud flow stays within the Vault trust boundary. The last scenario shows that a Vault-minted SPIFFE JWT can also work with an external relying party that only understands OIDC-style validation."

## Scenario 3: Relationship assistant with OIDC validation

### Business context

HashiBank wants an internal relationship-assistant experience for bankers. That assistant needs portable workload identity across system boundaries. The relying party should be able to validate the workload token without adopting Vault-native identity semantics.

### Operator steps

1. In the demo terminal, run:

   ```bash
   ./scripts/demo-agentic-oidc.sh
   ```

2. In the JSON output, point out these fields:
   - `validated_claims.sub`
   - `validated_claims.iss`
   - `validated_claims.aud`
   - `contexts`

3. Call out the SPIFFE subject value:

   ```text
   spiffe://hashibank.demo/ai/relationship-assistant
   ```

4. Open the browser page:

   ```text
   http://localhost:18082/
   ```

5. Point out the rendered fields on the page:
   - validated SPIFFE subject
   - issuer and audience
   - masked banker context for the assistant persona

### Suggested talk track

- "This workload also authenticates with AppRole and mints a SPIFFE JWT-SVID from `hashibank-identity`."
- "The relying party resolves the SPIFFE engine discovery document and JWKS endpoints."
- "It validates the JWT through OIDC-style patterns instead of Vault-native auth."
- "After validation, the assistant renders masked banking context for the banker experience."
- "This makes the AI or agentic story concrete. The identity model stays consistent even when the consumer sits outside Vault."

### What the audience should take away

- Vault-minted SPIFFE JWTs are not limited to Vault-to-Vault flows.
- OIDC-aware consumers can validate the tokens through published discovery and keys endpoints.
- SPIFFE gives the customer a portable workload identifier across tool boundaries.

## Closing talk track

Use this closing after the third scenario:

- "HashiBank uses Vault as the trust and policy plane, not as a generic token vending machine."
- "SPIFFE IDs sit above Vault entities and aliases as the portable workload identifier layer."
- "The demo shows three concrete outcomes: X.509 policy mapping, JWT-to-database access, and external OIDC-style validation."
- "If the customer needs deep workload attestation, pair Vault with an attestation system instead of over-claiming Vault alone."

## Reset after the demo

When you finish the session, run:

```bash
./scripts/teardown.sh
```

The teardown script stops the stack and removes generated runtime and TLS artifacts.
