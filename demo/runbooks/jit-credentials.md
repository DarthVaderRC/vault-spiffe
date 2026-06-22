# Runbook: Just in time credentials - Dynamic Workload Identities

**Theme.** Static credentials are the real risk in agentic and dynamically scaled systems. Vault issues short-lived, just-in-time identities and secrets tied to a verified workload, which shrinks the attack surface. You see dynamic secrets, identity brokering, and policy enforcement deliver secure, ephemeral access at runtime.

---
## Use case

The `relationship-assistant` workload needs to read customer relationship data from Postgres. Instead of a static database password baked into config, the workload:

1. Authenticates with its Kubernetes identity (no static secret).
2. Brokers a brand-new, short-lived Postgres user from Vault's database secrets engine, scoped to read-only relationship data.
3. Runs its query as that ephemeral user.
4. Has the credential revoked on demand, proving a leaked credential becomes useless.

This is the just-in-time, dynamic workload identity story end to end: verified identity in, ephemeral scoped secret out, with full lifecycle control.

## Demo recording

<!--
GitHub plays a video inline only when the file is uploaded as an attachment, not
when it is referenced by a committed relative path. To embed this recording:
  1. Open (or edit) a pull request or issue on this repository.
  2. Drag and drop `media/vault-jit-db-credentials-demo.mp4` into the comment box.
     GitHub uploads it and inserts a URL like
     https://github.com/user-attachments/assets/<uuid>.
  3. Copy that URL and replace the placeholder line below with it (URL on its own
     line). GitHub then renders an inline player.
The .mp4 is already H.264 / 1440p (GitHub's recommended codec) and ~6 MB, within
the 10 MB free-plan upload limit.
-->

> 📹 **Demo recording:** upload `media/vault-jit-db-credentials-demo.mp4` to a PR or issue comment and paste the resulting GitHub video URL on its own line here.

## Sequence diagram

![Just-in-time dynamic database credentials](../../media/sequence-k8s-jit.svg)

---

## Demo: `demo-k8s-jit.sh`

The demo is checkpointed and pauses after every call. It runs the database query and revocation proof from the `demo-tools` container; the workload identity is obtained by executing the Kubernetes login inside the actual `relationship-assistant` pod.

### Pre-flight

Run from the `demo/` directory.

```bash
# 1. Clean bootstrap (default Kubernetes-native path; no SPIRE needed for S2)
./scripts/teardown.sh
./scripts/bootstrap.sh

# 2. Smoke-test the full flow once, then reset so it is primed
./scripts/demo-k8s-jit.sh run
./scripts/demo-k8s-jit.sh reset
```

`bootstrap.sh` starts Postgres, seeds the `customer_relationships` table, configures the `database/` secrets engine with the `assistant-insights-readonly` dynamic role (default TTL 5m, max 30m), and attaches the `identity-assistant-k8s-jit` policy to the assistant's Kubernetes auth role.

Optional pre-brief for a technical audience: `./scripts/bootstrap.sh review` pages through the Vault config, including the dynamic database role.

### Live run

```bash
./scripts/demo-k8s-jit.sh run
```

Press `Enter` to advance — the demo pauses after every call, not just between the four checkpoints below.

| Step | Checkpoint | What appears on screen | What to say |
|------|-----------|------------------------|-------------|
| 1 | `kubernetes-login` | The assistant's Kubernetes auth role, its service account, and the Vault login response with `client_token` and policies `identity-assistant-k8s-spiffe` + `identity-assistant-k8s-jit`. | "The workload proves who it is with its Kubernetes service account token — no static secret. Vault returns a short-lived token carrying exactly the policies this identity is allowed." |
| 2 | `broker-db-creds` | The dynamic role definition (creation SQL, TTLs), then `vault read database/creds/assistant-insights-readonly` returning a fresh `username`, `password`, and `lease_id` with a 5-minute lease. | "Vault just created a brand-new Postgres user on demand, scoped to read-only relationship data, valid for five minutes. This credential did not exist a second ago and will not exist a few minutes from now." |
| 3 | `query-insights` | The query connects as the ephemeral `v-kubernet-assistan-…` user and returns the five relationship rows. | "The workload uses that just-in-time credential to do real work. Notice the database user is the exact ephemeral identity Vault minted — fully attributable in the database's own logs." |
| 4 | `revoke-lease` | `vault lease lookup` showing the expiry, `vault lease revoke`, then a reconnect attempt that fails with `password authentication failed`. | "Now the important part. Vault revokes the lease and the user is dropped. Even if an attacker had copied this credential, it is now dead. That is the difference between a static secret and a just-in-time identity." |

### Backup plan

- If the live cluster misbehaves, walk the pre-captured transcript and checkpoint JSON saved in the session backup (`k8s-jit.json` plus `jit-demo2.log`).
- `./scripts/demo-k8s-jit.sh status` shows which checkpoints have completed.
- Each step can be replayed individually, for example `./scripts/demo-k8s-jit.sh broker-db-creds`.
- If you need longer lease than 5 minutes, just rerun `./scripts/demo-k8s-jit.sh run`.
