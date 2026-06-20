# Session 2 runbook and slide outline: Just in time credentials — Dynamic Workload Identities

**Theme.** Static credentials are the real risk in agentic and dynamically scaled
systems. Vault issues short-lived, just-in-time identities and secrets tied to a
verified workload, which shrinks the attack surface. Attendees see dynamic
secrets, identity brokering, and policy enforcement deliver secure, ephemeral
access at runtime.

**Audience.** Clients, partners, and technical personnel (same audience as
Session 1).

**Format.** Short slot — about 15 minutes of talk plus a 10 minute demo.

**Relationship to Session 1.** Session 1 ("Your agents are looking SPIFFE")
establishes the SPIFFE identity foundation and the `demo-k8s-jwt` flow. Session 2
reuses the *same* verified Kubernetes workload identity and shows what Vault does
*after* identity: it brokers a just-in-time database credential. Open Session 2
with a one-slide recap, then pivot straight to dynamic secrets so you do not
repeat the Session 1 introduction.

---

## Use case

The `relationship-assistant` workload is already a verified identity from
Session 1. In this session it needs to read customer relationship data from
Postgres. Instead of a static database password baked into config, the workload:

1. Authenticates with its Kubernetes identity (no static secret).
2. Brokers a brand-new, short-lived Postgres user from Vault's database secrets
   engine, scoped to read-only relationship data.
3. Runs its query as that ephemeral user.
4. Has the credential revoked on demand, proving a leaked credential becomes
   useless.

This is the just-in-time, dynamic workload identity story end to end: verified
identity in, ephemeral scoped secret out, with full lifecycle control.

## Sequence

![Just-in-time dynamic database credentials](../../media/sequence-k8s-jit.svg)

---

## Demo: `demo-k8s-jit.sh`

The demo is checkpointed and pauses between steps so you can narrate. It runs the
database query and revocation proof from the `demo-tools` container; the workload
identity is obtained by executing the Kubernetes login inside the actual
`relationship-assistant` pod.

### Pre-flight (before the room is live)

Run from the `demo/` directory.

```bash
# 1. Clean bootstrap (default Kubernetes-native path; no SPIRE needed for S2)
./scripts/teardown.sh
./scripts/bootstrap.sh

# 2. Smoke-test the full flow once, then reset so it is primed
./scripts/demo-k8s-jit.sh run
./scripts/demo-k8s-jit.sh reset
```

`bootstrap.sh` starts Postgres, seeds the `customer_relationships` table,
configures the `database/` secrets engine with the `assistant-insights-readonly`
dynamic role (default TTL 5m, max 30m), and attaches the
`identity-assistant-k8s-jit` policy to the assistant's Kubernetes auth role.

Optional pre-brief for a technical audience: `./scripts/bootstrap.sh review`
pages through the Vault config, including the dynamic database role.

### Live run

```bash
./scripts/demo-k8s-jit.sh run
```

Press `n` to advance between the four checkpoints.

| Step | Checkpoint | What appears on screen | What to say |
|------|-----------|------------------------|-------------|
| 1 | `kubernetes-login` | The assistant's Kubernetes auth role, its service account, and the Vault login response with `client_token` and policies `identity-assistant-k8s-spiffe` + `identity-assistant-k8s-jit`. | "The workload proves who it is with its Kubernetes service account token — no static secret. Vault returns a short-lived token carrying exactly the policies this identity is allowed." |
| 2 | `broker-db-creds` | The dynamic role definition (creation SQL, TTLs), then `vault read database/creds/assistant-insights-readonly` returning a fresh `username`, `password`, and `lease_id` with a 5-minute lease. | "Vault just created a brand-new Postgres user on demand, scoped to read-only relationship data, valid for five minutes. This credential did not exist a second ago and will not exist a few minutes from now." |
| 3 | `query-insights` | The query connects as the ephemeral `v-kubernet-assistan-…` user and returns the five relationship rows. | "The workload uses that just-in-time credential to do real work. Notice the database user is the exact ephemeral identity Vault minted — fully attributable in the database's own logs." |
| 4 | `revoke-lease` | `vault lease lookup` showing the expiry, `vault lease revoke`, then a reconnect attempt that fails with `password authentication failed`. | "Now the important part. Vault revokes the lease and the user is dropped. Even if an attacker had copied this credential, it is now dead. That is the difference between a static secret and a just-in-time identity." |

### Backup plan

- If the live cluster misbehaves, walk the pre-captured transcript and checkpoint
  JSON saved in the session backup (`k8s-jit.json` plus `jit-demo2.log`).
- `./scripts/demo-k8s-jit.sh status` shows which checkpoints have completed.
- Each step can be replayed individually, for example
  `./scripts/demo-k8s-jit.sh broker-db-creds`.
- The 5-minute lease is comfortable for a 10-minute slot, but if you linger past
  the lease on steps 3 or 4, just rerun `./scripts/demo-k8s-jit.sh run`.

### Timing

Aim for roughly two minutes per step. The revocation step is the closer — give it
room to land.

---

## Slide outline

Use the `MGL-SPIFFE-with-Vault-Prod.pptx` template. Cut the TPM slides (24–26) and
the agentic control plane and Agent Registry roadmap slides (27–38). Session 2 is
deliberately lean: a short recap, then the dynamic-secrets story.

### Reuse from the existing deck

| Existing slide | Use as | Note |
|----------------|--------|------|
| 1 (title) | Retitle for this session | See M1 below |
| 15 "Authorization challenges with SPIRE" | The setup for "identity is not enough" | Lead with the "No secret storage" and "No lifecycle management for external secrets" points — they justify Vault as the broker |
| 21 "Vault as SPIFFE provider" | Where dynamic access fits | Highlight the "Secrets Access" quadrant: dynamic DB creds, PKI, cloud IAM |
| 43 "Vault current capabilities" | Wrap / appendix | Reinforce that this is shipping product |

### New slides

- **M1 — Session title.** "Just in time credentials: Dynamic Workload
  Identities." Subtitle: "From verified identity to ephemeral, scoped access."

- **M2 — Static credentials are the risk.** Three failure modes of static secrets
  in dynamic and agentic systems: long-lived shared passwords, credentials sitting
  in config and exfiltrable from disk, and no per-workload attribution. One line:
  "Workloads scale and disappear in seconds; credentials should too."

- **M3 — Recap bridge (one slide).** "In Session 1 we gave the workload a verified
  SPIFFE identity. Now: what access does that identity get?" Small diagram: K8s
  auth → verified identity → (this session) dynamic secret. Keeps continuity
  without repeating Session 1.

- **M4 — Dynamic secrets and identity brokering.** The concept slide. Vault as the
  broker between a verified identity and the target system: short-lived, unique
  per request, automatically revoked, fully audited. Contrast a static password
  (one secret, shared, forever) with a dynamic credential (minted per request,
  scoped, expiring).

- **M5 — JIT demo architecture.** The flow the audience is about to see:
  `relationship-assistant` → Kubernetes auth → Vault database secrets engine →
  ephemeral Postgres user → query → lease revoked. Reuse the editorial flow style
  from `media/workflow-k8s-jwt.svg` as a starting point and relabel the downstream
  to Postgres dynamic credentials.

- **M6 — Policy and lifecycle guarantees.** Close the loop: the
  `identity-assistant-k8s-jit` policy scopes which role the workload can read; the
  dynamic role enforces read-only SQL and a 5-minute TTL; Vault revokes on demand;
  every issue and revoke is in the audit log. One line on attack-surface
  reduction: "A leaked credential is worthless minutes later, and revocable
  instantly."

### Optional bridge to Session 1 and the IBM Verify session

If both sessions run back to back, add a single closing line, not a slide: "The
same Vault-minted identity can also broker cloud credentials through token
exchange — which is exactly what the IBM Verify session covers next."
