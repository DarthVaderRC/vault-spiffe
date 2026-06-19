# Session 1 runbook and slide outline: Your agents are looking SPIFFE — Dynamic Agent Identities

**Theme.** SPIFFE IDs and SVIDs uniquely identify AI agents and services, which
enables secure service-to-service communication without static credentials.
Vault establishes zero-trust identity for non-human actors and acts as both the
identity issuer and the identity broker.

**Audience.** Clients, partners, and technical personnel.

**Format.** Short slot — about 15 minutes of talk plus a 10 minute demo.

**Relationship to the other sessions.** This session owns the full SPIFFE and
SPIRE foundation. Session 2 ("Just in time credentials") reuses the same verified
identity to broker dynamic database credentials, so you do not need to cover
dynamic secrets here. A separate IBM Verify session covers SPIFFE-based OBO OAuth2
token exchange with an external identity provider, so keep external-IdP token
exchange to a single bridge slide and do not demo it live.

---

## Use case

Reframe the existing `demo-k8s-jwt` flow as dynamic agent identity for
service-to-service authentication. The `relationship-assistant` workload is the
"agent." It authenticates with its platform-native Kubernetes identity, Vault
mints a short-lived JWT-SVID with a unique `spiffe://` subject and business
metadata, and a downstream API authorizes the call by validating that token
through Vault's OIDC discovery and JWKS endpoints. No static credential is shared
between the two services, and every call is attributable to a specific workload
identity.

This is the cleanest way to show Vault as both the identity issuer (the SPIFFE
secrets engine mints the JWT-SVID) and the trust anchor that downstream services
validate against (discovery and JWKS), which is the core Vault-plus-SPIFFE
message.

---

## Demo: `demo-k8s-jwt.sh`

The demo is checkpointed and pauses between steps so you can narrate. The
Kubernetes login and JWT mint run inside the actual `relationship-assistant` pod;
discovery and the downstream API call show the validation path.

### Pre-flight (before the room is live)

Run from the `demo/` directory.

```bash
# 1. Clean bootstrap (default Kubernetes-native path)
./scripts/teardown.sh
./scripts/bootstrap.sh

# 2. Smoke-test the full flow once, then reset so it is primed
./scripts/demo-k8s-jwt.sh run
./scripts/demo-k8s-jwt.sh reset
```

Optional pre-brief for a technical audience: `./scripts/bootstrap.sh review`
pages through the Kubernetes auth roles, PKI roles, and the SPIFFE engine config.

### Live run

```bash
./scripts/demo-k8s-jwt.sh run
```

Press `n` to advance between the four checkpoints.

| Step | Checkpoint | What appears on screen | What to say |
|------|-----------|------------------------|-------------|
| 1 | `kubernetes-login` | The assistant's Kubernetes auth role, its service account, and the Vault login response with a `client_token`. | "The agent authenticates with its Kubernetes service account token — its platform-native identity. No static secret. Vault hands back a short-lived token." |
| 2 | `mint-jwt` | The SPIFFE role template, then the minted JWT-SVID and its decoded claims: a `spiffe://hashibank.demo/ns/assistants/sa/relationship-assistant` subject plus `bank`, `application`, `line_of_business`, `environment`, and `customer_data_domain`. | "Vault mints a JWT-SVID for this exact workload. The subject is a portable SPIFFE ID, and we attach business context the downstream service can authorize on — not just 'who', but 'what kind of workload'." |
| 3 | `fetch-discovery` | Vault's OIDC discovery document and JWKS, with the issuer and `jwks_uri`. | "Any service can validate this token without calling Vault on the hot path. It reads the standard discovery document, fetches the public keys from JWKS, and verifies the signature locally." |
| 4 | `call-consumer` | The agent calls the `jwt-consumer` relationship insights API; the response shows `validated_claims`, masked relationship insights, and a next-best action. | "The downstream API validated the JWT-SVID, authorized the claims, and returned protected data. That is zero-trust service-to-service auth — the identity is useful because another service actually consumes and authorizes it, with no shared secret." |

### Backup plan

- If the live cluster misbehaves, walk the pre-captured transcript and checkpoint
  JSON saved in the session backup (`k8s-jwt.json` plus `jwt-demo.log`).
- `./scripts/demo-k8s-jwt.sh status` shows which checkpoints have completed.
- Each step can be replayed individually, for example
  `./scripts/demo-k8s-jwt.sh mint-jwt`.
- If a JWT expires while you linger, rerun `./scripts/demo-k8s-jwt.sh run`.

### Timing

Aim for roughly two minutes per step. The downstream authorized response in step 4
is the payoff — slow down there.

---

## Slide outline

Use the `MGL-SPIFFE-with-Vault-Prod.pptx` template. Cut the TPM slides (24–26) and
the agentic control plane and Agent Registry roadmap slides (27–38), with at most
one bridge slide kept for context.

### Reuse from the existing deck

| Existing slide | Use as | Note |
|----------------|--------|------|
| 1 (title) | Retitle for this session | See N1 below |
| 4 + 6 (Macquarie) | Customer proof | LDAP to ephemeral SPIFFE identities; concrete outcomes |
| 8 "What is SPIFFE" | Framework essentials | SPIFFE ID, SVID, trust bundle, Workload API |
| 9 "Where SPIFFE delivers value" | Optional | Includes agentic identity; useful for this audience |
| 13 "SPIFFE with SPIRE" + 14 "SPIRE limitations" | What SPIRE is and its cost | Sets up "identity is not enough" |
| 15 "Authorization challenges with SPIRE" | Identity is not authorization | Bridges to where Vault fits |
| 17 "Why SPIFFE with Vault" | One auth surface, one policy, one audit log | Core positioning |
| 21 "Vault as SPIFFE provider" | Issuer and broker | SPIFFE auth method and SPIFFE secrets engine |
| 22 "Vault platform attestation" | How Vault verifies identity | Kubernetes, AWS, GCP, Azure — skip the TPM future row |
| 43–45 (capabilities and samples) | Wrap / appendix | Shipping product, sample JWT-SVID and X.509 |

### New slides

- **N1 — Session title.** "Your agents are looking SPIFFE: Dynamic Agent
  Identities." Subtitle: "Unique, short-lived, verifiable identity for non-human
  actors."

- **N2 — Agents need identity too.** Condense the agentic identity problem into one
  slide (draw from existing slides 29 and 31): two replicas of the same agent
  share one static identity, so you cannot attribute actions or scope access.
  The fix: a unique, signed, short-lived SPIFFE identity per workload or replica,
  assigned by the platform, not the application code.

- **N3 — Demo architecture.** The flow the audience is about to see: agent
  (`relationship-assistant`) → Kubernetes auth → Vault SPIFFE secrets engine mints
  a JWT-SVID → downstream API validates via discovery and JWKS and authorizes the
  claims. Reuse `media/workflow-k8s-jwt.svg` or `media/demo-architecture-k8s-anchor.svg`.

- **N4 — Extend the pattern (bridge slide, no live demo).** The same Vault-minted
  JWT-SVID is portable to any OIDC-compliant verifier: Entra ID workload identity
  federation, IBM Verify, or AWS STS for temporary cloud credentials. Reference
  the Entra ID token-exchange flow (existing slides 39–41 and the
  `patshash/artifactory-vault` Azure example). Use this to hand off to the IBM
  Verify session: "Next, you will see this exact identity exchanged with an
  external IdP."

---

## How the two sessions fit together

| | Session 1 | Session 2 |
|---|-----------|-----------|
| Headline | Dynamic agent identity | Just-in-time credentials |
| Question | How do workloads prove who they are without static secrets? | Once verified, how do they get scoped, ephemeral access? |
| Demo | `demo-k8s-jwt.sh` | `demo-k8s-jit.sh` |
| Vault role | Identity issuer and trust anchor | Identity broker for dynamic secrets |
| Proof point | Downstream API authorizes a JWT-SVID | Ephemeral Postgres user issued, used, and revoked |

Run Session 1 first. It carries the SPIFFE and SPIRE foundation. Session 2 opens
with a one-slide recap and goes straight to dynamic secrets.
