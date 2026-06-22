#!/usr/bin/env python3
"""Generate Carbon-style sequence diagrams for the demo flows.

Matches the visual language of media/sequence-k8s-jwt.svg and
media/sequence-k8s-jit.svg (white background, blue left rail, IBM Plex,
colored lifeline headers, activation bars, numbered request/response arrows,
bent self-calls, mono footer). Each diagram is declared as a list of
participants plus an ordered list of messages, so the geometry is computed
consistently instead of hand-placed.

Usage:
    python3 media/sequence_diagrams.py          # write all SVGs to media/
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field
from pathlib import Path

MEDIA_DIR = Path(__file__).resolve().parent

# ---- palette (Carbon) -------------------------------------------------------
INK = "#161616"
SUBTLE = "#525252"
RAIL = "#0f62fe"
LIFELINE = "#a8a8a8"
ACT_FILL = "#edf5ff"
ACT_STROKE = "#a6c8ff"
RESP = "#525252"
BLUE = "#0043ce"
RED = "#da1e28"

# ---- geometry ---------------------------------------------------------------
HEAD_TOP = 132
HEAD_H = 64
HEAD_BOTTOM = HEAD_TOP + HEAD_H  # 196
ROW0 = 242
ROWSTEP = 52
ACT_W = 9


@dataclass
class Participant:
    name: str
    sub: str
    color: str


@dataclass
class Message:
    frm: int          # participant index
    to: int           # participant index (== frm for a self-call)
    label: str
    sub: str = ""
    kind: str = "request"   # request | response | response-blue | response-red | self


@dataclass
class Diagram:
    slug: str
    title: str
    subtitle: str
    footer: str
    participants: list[Participant]
    messages: list[Message] = field(default_factory=list)


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _centers(n: int) -> tuple[list[float], int]:
    if n <= 3:
        side, spacing = 190, 250
    else:
        side, spacing = 150, 230
    centers = [side + i * spacing for i in range(n)]
    width = int(centers[-1] + side)
    return centers, width


def render(d: Diagram) -> str:
    centers, width = _centers(len(d.participants))
    box_w = 188 if len(d.participants) <= 3 else 188
    rows = [ROW0 + i * ROWSTEP for i in range(len(d.messages))]

    # vertical extent: account for activation bars (req -> +58) and self loops (+18)
    max_y = rows[-1] if rows else HEAD_BOTTOM
    for i, m in enumerate(d.messages):
        if m.kind == "request":
            max_y = max(max_y, rows[i] - 6 + 58)
        elif m.kind == "self":
            max_y = max(max_y, rows[i] + 18)
    lifeline_bottom = int(max_y + 20)
    footer_y = lifeline_bottom + 34
    height = footer_y + 22

    out: list[str] = []
    out.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="{_esc(d.title)}">'
    )
    out.append(
        '<defs><style type="text/css"><![CDATA[@import '
        "url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600;700&"
        "family=IBM+Plex+Mono:wght@400;500&display=swap');]]></style>"
        '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" '
        f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{INK}"/></marker>'
        '<marker id="arrowblue" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" '
        f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{BLUE}"/></marker>'
        '<marker id="arrowred" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" '
        f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{RED}"/></marker></defs>'
    )
    out.append(f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>')
    out.append(f'<rect x="0" y="0" width="6" height="{height}" fill="{RAIL}"/>')
    out.append(
        f'<text x="48" y="56" font-family="IBM Plex Sans" font-size="26" font-weight="700" '
        f'fill="{INK}">{_esc(d.title)}</text>'
    )
    out.append(
        f'<text x="48" y="86" font-family="IBM Plex Sans" font-size="15" '
        f'fill="{SUBTLE}">{_esc(d.subtitle)}</text>'
    )
    out.append(f'<line x1="48" y1="104" x2="128" y2="104" stroke="{RAIL}" stroke-width="3"/>')

    # lifelines + headers
    for idx, p in enumerate(d.participants):
        cx = centers[idx]
        bx = cx - box_w / 2
        out.append(
            f'<line x1="{cx}" y1="{HEAD_BOTTOM}" x2="{cx}" y2="{lifeline_bottom}" '
            f'stroke="{LIFELINE}" stroke-width="1.5" stroke-dasharray="2 5"/>'
        )
        out.append(
            f'<rect x="{bx}" y="{HEAD_TOP}" width="{box_w}" height="{HEAD_H}" rx="4" '
            f'fill="#ffffff" stroke="{p.color}" stroke-width="1.6"/>'
        )
        out.append(f'<rect x="{bx}" y="{HEAD_TOP}" width="{box_w}" height="5" rx="2" fill="{p.color}"/>')
        name_size = 14.5 if len(p.name) <= 17 else 13
        out.append(
            f'<text x="{cx}" y="162" text-anchor="middle" font-family="IBM Plex Sans" '
            f'font-size="{name_size}" font-weight="700" fill="{INK}">{_esc(p.name)}</text>'
        )
        out.append(
            f'<text x="{cx}" y="182" text-anchor="middle" font-family="IBM Plex Mono" '
            f'font-size="10.5" fill="{SUBTLE}">{_esc(p.sub)}</text>'
        )

    # activation bars (on the destination of each request)
    for i, m in enumerate(d.messages):
        if m.kind != "request" or m.frm == m.to:
            continue
        cx = centers[m.to]
        y = rows[i] - 6
        out.append(
            f'<rect x="{cx - ACT_W / 2}" y="{y}" width="{ACT_W}" height="58" rx="1.5" '
            f'fill="{ACT_FILL}" stroke="{ACT_STROKE}" stroke-width="1"/>'
        )

    # messages
    for i, m in enumerate(d.messages):
        y = rows[i]
        if m.kind == "self":
            cx = centers[m.frm]
            color = BLUE
            if m.frm == 0:
                # leftmost participant: loop to the right, label right, badge left
                out.append(
                    f'<path d="M{cx + 4.5},{y} h34 v18 h-34" fill="none" stroke="{color}" '
                    f'stroke-width="1.6" marker-end="url(#arrowblue)"/>'
                )
                out.append(
                    f'<text x="{cx + 48}" y="{y + 7}" text-anchor="start" font-family="IBM Plex Sans" '
                    f'font-size="12.5" font-style="italic" fill="{SUBTLE}">{_esc(m.label)}</text>'
                )
                badge_x = cx - 20.5
            else:
                out.append(
                    f'<path d="M{cx - 4.5},{y} h-34 v18 h34" fill="none" stroke="{color}" '
                    f'stroke-width="1.6" marker-end="url(#arrowblue)"/>'
                )
                out.append(
                    f'<text x="{cx - 48}" y="{y + 7}" text-anchor="end" font-family="IBM Plex Sans" '
                    f'font-size="12.5" font-style="italic" fill="{SUBTLE}">{_esc(m.label)}</text>'
                )
                badge_x = cx + 20.5
        else:
            sx = centers[m.frm]
            dx = centers[m.to]
            rightward = dx > sx
            x1 = sx + 4.5 if rightward else sx - 4.5
            x2 = dx - 4.5 if rightward else dx + 4.5
            mid = (sx + dx) / 2
            if m.kind == "request":
                stroke, marker, dash, weight, lab_fill = INK, "arrow", "", "600", INK
            elif m.kind == "response-blue":
                stroke, marker, dash, weight, lab_fill = BLUE, "arrowblue", ' stroke-dasharray="6 4"', "400", SUBTLE
            elif m.kind == "response-red":
                stroke, marker, dash, weight, lab_fill = RED, "arrowred", ' stroke-dasharray="6 4"', "400", SUBTLE
            else:  # response
                stroke, marker, dash, weight, lab_fill = RESP, "arrow", ' stroke-dasharray="6 4"', "400", SUBTLE
            out.append(
                f'<line x1="{x1}" y1="{y}" x2="{x2}" y2="{y}" stroke="{stroke}" '
                f'stroke-width="1.7"{dash} marker-end="url(#{marker})"/>'
            )
            out.append(
                f'<text x="{mid}" y="{y - 9}" text-anchor="middle" font-family="IBM Plex Sans" '
                f'font-size="12.5" font-weight="{weight}" fill="{lab_fill}">{_esc(m.label)}</text>'
            )
            if m.sub:
                out.append(
                    f'<text x="{mid}" y="{y + 15}" text-anchor="middle" font-family="IBM Plex Mono" '
                    f'font-size="10" fill="{SUBTLE}">{_esc(m.sub)}</text>'
                )
            badge_x = (sx - 11.5) if rightward else (sx + 11.5)
        # number badge at the source side
        out.append(
            f'<g><circle cx="{badge_x}" cy="{y}" r="9" fill="{RAIL}"/>'
            f'<text x="{badge_x}" y="{y + 3.5}" text-anchor="middle" font-family="IBM Plex Mono" '
            f'font-size="10.5" font-weight="500" fill="#ffffff">{i + 1}</text></g>'
        )

    out.append(
        f'<text x="48" y="{footer_y}" font-family="IBM Plex Mono" font-size="11" '
        f'fill="{SUBTLE}">{_esc(d.footer)}</text>'
    )
    out.append("</svg>")
    return "\n".join(out) + "\n"


# ---- diagram definitions ----------------------------------------------------
def diagrams() -> list[Diagram]:
    out: list[Diagram] = []

    # 1. Zero-trust mTLS between Kubernetes microservices
    out.append(Diagram(
        slug="sequence-k8s-mtls",
        title="Zero-trust mTLS between Kubernetes microservices",
        subtitle="Kubernetes auth -> Vault PKI X.509-SVIDs -> mutual SPIFFE-ID verification",
        footer="Workload identity - both peers present Vault-issued SPIFFE certificates",
        participants=[
            Participant("payments-api", "frontend workload", "#0f62fe"),
            Participant("Vault", "hashibank.demo", "#8a3ffc"),
            Participant("mtls-backend", "backend workload", "#007d79"),
        ],
        messages=[
            Message(0, 1, "POST auth/kubernetes/login", "projected SA token", "request"),
            Message(1, 0, "Vault client token", "scoped PKI policy", "response"),
            Message(0, 1, "POST pki/issue/payments-k8s-spiffe", "uri_sans=spiffe://.../payments-api", "request"),
            Message(1, 0, "frontend X.509-SVID", "cert + key, SPIFFE URI SAN", "response"),
            Message(1, 2, "backend X.509-SVID (issued at startup)", "spiffe://.../mtls-backend", "response"),
            Message(0, 2, "mutual-TLS GET /api/payments/status", "both present SPIFFE SVIDs", "request"),
            Message(2, 2, "verify client SPIFFE-ID, authorize", "", "self"),
            Message(2, 0, "200 payment status", "authorized peer + payment_reference", "response-blue"),
        ],
    ))

    # 2. SPIRE JWT-SVID to Vault auth and dynamic Postgres access
    out.append(Diagram(
        slug="sequence-spire-jwt",
        title="SPIRE JWT-SVID to Vault auth and dynamic DB access",
        subtitle="SPIRE Workload API -> Vault SPIFFE JWT login -> dynamic Postgres credentials",
        footer="SPIRE identity becomes a Vault token, then a short-lived Postgres login, then fraud data",
        participants=[
            Participant("vault-spire-client", "fraud workload", "#0f62fe"),
            Participant("SPIRE Agent", "Workload API", "#ff832b"),
            Participant("Vault", "hashibank.demo", "#8a3ffc"),
            Participant("Postgres", "fraud_alerts", "#007d79"),
        ],
        messages=[
            Message(0, 1, "fetch jwt -audience vault-spire-demo", "Workload API socket", "request"),
            Message(1, 0, "JWT-SVID", "sub spiffe://spire.hashibank.demo/...", "response"),
            Message(0, 2, "POST auth/spire-jwt/login", "Authorization: Bearer JWT-SVID", "request"),
            Message(2, 0, "Vault client token", "fraud-readonly policy", "response"),
            Message(0, 2, "GET database/creds/fraud-readonly", "vault token", "request"),
            Message(2, 0, "dynamic Postgres user", "username/password, lease TTL", "response"),
            Message(0, 3, "SELECT ... FROM fraud_alerts", "login as ephemeral user", "request"),
            Message(3, 0, "fraud alert rows", "rendered to fraud dashboard", "response-blue"),
        ],
    ))

    # 3. Vault as SPIRE upstream authority
    out.append(Diagram(
        slug="sequence-spire-upstreamauthority",
        title="Vault as SPIRE upstream authority",
        subtitle="SPIRE signs SVIDs under a Vault-managed root -> chain verified with openssl",
        footer="X.509 CA delegation - the SPIRE workload SVID chains back to the Vault root",
        participants=[
            Participant("Vault", "spire-pki (root CA)", "#8a3ffc"),
            Participant("SPIRE Agent", "SVID issuer", "#ff832b"),
            Participant("Verifier", "openssl verify", "#007d79"),
        ],
        messages=[
            Message(0, 1, "upstream intermediate via upstreamauthority_vault", "SPIRE signs under Vault root", "response"),
            Message(2, 0, "GET spire-pki/cert/ca", "Vault-managed SPIRE root", "request"),
            Message(0, 2, "SPIRE root CA", "fingerprint == SPIRE bootstrap bundle", "response"),
            Message(2, 1, "spire-agent api fetch x509", "Workload API socket", "request"),
            Message(1, 2, "X.509-SVID chain", "leaf + issuing intermediate", "response"),
            Message(2, 2, "openssl verify -> chains to Vault root", "", "self"),
        ],
    ))

    # 4. SPIFFE engine on performance replica
    out.append(Diagram(
        slug="sequence-perf-replica-issuer",
        title="SPIFFE issuer on a Vault performance replica",
        subtitle="Replicated SPIFFE mount without jwt_issuer_url -> observe the minted iss claim",
        footer="Performance replication - which cluster address shows up as the JWT issuer?",
        participants=[
            Participant("Primary Vault", "hashibank-vault", "#8a3ffc"),
            Participant("Performance Replica", "hashibank-vault-perf", "#0f62fe"),
            Participant("AppRole client", "issuer check", "#007d79"),
        ],
        messages=[
            Message(0, 0, "enable performance primary", "", "self"),
            Message(0, 1, "secondary token + enable secondary", "activation token", "request"),
            Message(0, 1, "replicate SPIFFE mount + AppRole", "spiffe-default-issuer (no jwt_issuer_url)", "response"),
            Message(2, 1, "POST auth/approle/login", "role_id + secret_id", "request"),
            Message(1, 2, "Vault client token", "scoped issuer policy", "response"),
            Message(2, 1, "POST spiffe-default-issuer/role/.../mintjwt", "audience=perf-replica-issuer-check", "request"),
            Message(1, 2, "JWT-SVID", "iss claim under inspection", "response"),
            Message(2, 2, "decode iss == replica API address", "", "self"),
        ],
    ))

    return out


def main() -> None:
    for d in diagrams():
        path = MEDIA_DIR / f"{d.slug}.svg"
        path.write_text(render(d), encoding="utf-8")
        print(f"wrote {path.relative_to(MEDIA_DIR.parent)}")


if __name__ == "__main__":
    main()
