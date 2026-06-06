# Architecture Site

A single-page, zero-dependency architecture guide for **Vault as Control Plane for SPIFFE Identities**. The site is designed for GitHub Pages: one HTML file, no build step, IBM Carbon-influenced page chrome, and inline SVG anchor visuals.

## Serve locally

```bash
python3 -m http.server --directory docs 8080
# open http://localhost:8080
```

You can also open `index.html` directly in a browser, but a local server is better for keyboard interactions and future asset additions.

## Scope

This iteration ships the full narrative spine:

1. Problem
2. Foundations
3. Zero-trust mTLS between Kubernetes microservices
4. Cross-network API authentication using JWT-SVID
5. SPIRE JWT-SVID to Vault auth and dynamic database credentials
6. Vault as SPIRE upstream authority
7. AWS web identity federation as a follow-on pattern
8. Runbook

## Editing

Everything is inline in `index.html`:

- CSS in `<style>`
- SVGs in markup
- behavior in a single `<script>`

No bundler, no framework, and no separate asset pipeline.
