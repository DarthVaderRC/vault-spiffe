# vault-spiffe

A runnable local HashiBank demo for showing how **HashiCorp Vault** fits into a broader **SPIFFE / non-human identity** strategy.

This repository combines:

- a source-backed design spec
- a Docker Compose demo built on a single **HashiBank Vault Cluster**
- interactive HTML artifacts for exploring the deck and the repository architecture

## What this repository covers

The repo is centered on three proof flows:

1. **Payments API X.509**
   - AppRole login
   - Vault PKI issues a certificate with a SPIFFE URI SAN
   - SPIFFE X.509 auth maps that identity to a payments policy
   - the workload reads payments API KV secrets
2. **Fraud Ops JWT-SVID**
   - AppRole login with alias metadata
   - Vault SPIFFE secrets mint a JWT-SVID
   - SPIFFE JWT auth exchanges that workload identity for a Vault token
   - the workload reads dynamic Postgres credentials and reveals banking data
3. **Relationship assistant OIDC validation**
   - Vault mints a SPIFFE JWT
   - the SPIFFE engine exposes discovery + JWKS
   - a downstream service validates the JWT outside Vault and renders masked banker context

## Repository layout

| Path | Purpose |
| --- | --- |
| `requirements.md` | Original business context and customer ask |
| `spec.md` | Spec-driven design for the customer content and runnable demo |
| `content/vault-spiffe-customer-deck.md` | Customer deck manuscript in markdown |
| `demo/README.md` | Presenter-oriented demo setup and operator runbook |
| `demo/DEMO_WALKTHROUGH.md` | Live-demo talk track and highlight cues |
| `demo/` | Docker Compose lab, bootstrap scripts, Python scenario runners, and web apps |
| `playgrounds/customer-deck-presentation.html` | Interactive deck explorer built from the customer deck manuscript |
| `playgrounds/repo-code-explorer.html` | Interactive code-map explorer for the repository architecture |

## Quick start

### Prerequisites

- Docker Desktop or Docker Engine with Compose v2
- a Vault Enterprise license file at `license.hclic`

### Bootstrap the demo

```bash
cd demo
./scripts/bootstrap.sh
```

Review the configured environment before running the scenarios:

```bash
./scripts/bootstrap.sh review
```

### Run the demo scenarios

```bash
./scripts/demo-x509-payments.sh
./scripts/demo-jwt-fraud.sh
./scripts/demo-agentic-oidc.sh
```

Or run step-by-step checkpoints:

```bash
./scripts/demo-x509-payments.sh approle-login
./scripts/demo-jwt-fraud.sh mint-jwt
./scripts/demo-agentic-oidc.sh validate-jwt
```

### Tear down

```bash
./scripts/teardown.sh
```

## Interactive artifacts

Open the generated HTML tools locally:

```bash
open playgrounds/customer-deck-presentation.html
open playgrounds/repo-code-explorer.html
```

## Important implementation notes

- The demo uses **one Vault cluster**: `hashibank-vault`.
- The X.509 flow uses **Vault PKI with SPIFFE URI SANs**; it does **not** claim native X.509 SVID issuance from the SPIFFE secrets engine.
- The JWT flows use the **SPIFFE secrets engine** for JWT-SVID minting and **SPIFFE auth** for login.
- SCIM and SAML are covered in the customer deck as **adjacent identity topics**, not as the main runnable demo path.

## Where to start reading

- Start with `spec.md` if you want the design rationale.
- Start with `content/vault-spiffe-customer-deck.md` if you want the customer-facing narrative.
- Start with `demo/README.md` if you want to run the lab.
