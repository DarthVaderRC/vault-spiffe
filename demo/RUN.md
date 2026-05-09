## SPIFFE-SPIRE
![](../content/SPIFFE-SPIRE.png)
![](../content/SPIFFE-SPIRE-architecture.png)
## Logical flow

```mermaid
flowchart LR
  subgraph Issuer["HashiBank Identity"]
    approle["AppRole auth"]
    pki["PKI engine"]
    spiffe["SPIFFE secrets engine"]
    identity["Identity / alias metadata"]
  end

  subgraph Target["HashiBank Access"]
    spiffeAuth["SPIFFE auth"]
    dbEngine["Database secrets engine"]
    paymentsProof["Payments proof secret"]
  end

  tools["demo-tools / payments-api persona"]
  fraud["hashibank-fraud-web"]
  assistant["hashibank-assistant"]
  postgres["postgres-hashibank"]

  tools --> approle
  fraud --> approle
  assistant --> approle
  approle --> identity
  tools --> pki
  fraud --> spiffe
  assistant --> spiffe
  pki --> spiffeAuth
  spiffe --> spiffeAuth
  spiffeAuth --> dbEngine
  dbEngine --> postgres
  spiffeAuth --> paymentsProof
  fraud --> postgres

  style Issuer fill:#E8F0FE,stroke:#5B8DEF,stroke-width:1px,color:#111827
  style Target fill:#EAF7EE,stroke:#57A773,stroke-width:1px,color:#111827
```

## Slide 10 - HashiBank demo architecture

**Title:** Demo section: HashiBank architecture

**On-slide content**

- **Trust domain:** `hashibank.demo`
- **Identity plane:** `hashibank-identity`
- **Access plane:** `hashibank-access`

```mermaid
flowchart LR
  subgraph Identity["HashiBank Identity"]
    A["AppRole"]
    P["PKI"]
    J["SPIFFE JWT minting"]
    M["Alias metadata"]
  end

  subgraph Access["HashiBank Access"]
    X["SPIFFE X.509 auth"]
    Y["SPIFFE JWT auth"]
    D["Database secrets"]
    K["KV proof secret"]
  end

  PG["Postgres / fraud_alerts"]
  F["hashibank-fraud-web"]
  R["hashibank-assistant"]
  T["payments-api script"]

  T --> A
  T --> P
  F --> A
  F --> J
  R --> A
  R --> J
  P --> X
  J --> Y
  Y --> D --> PG
  X --> K
```

---

## Slide 11 - Demo 1: Payments API with X.509 SPIFFE auth

**Title:** Demo 1: `payments-api` gets policy through X.509 SPIFFE auth

**On-slide content**

```mermaid
sequenceDiagram
  participant Payments as payments-api
  box rgb(232, 240, 254) HashiBank Identity
    participant AppRole as AppRole
    participant PKI as PKI
  end
  box rgb(234, 247, 238) HashiBank Access
    participant SPIFFEX as SPIFFE X.509 auth
    participant Proof as payments proof secret
  end

  Payments->>AppRole: Login
  AppRole-->>Payments: Vault token
  Payments->>PKI: Issue cert with spiffe://hashibank.demo/payments/api
  PKI-->>Payments: X.509 cert + key
  Payments->>SPIFFEX: Login with client cert
  SPIFFEX-->>Payments: payments-scoped Vault token
  Payments->>Proof: Read proof secret
```

---

## Slide 12 - Demo 2: Fraud Ops JWT-SVID to dynamic Postgres credentials

**Title:** Demo 2: `fraud-ops-web` turns JWT identity into live banking data

**On-slide content**

```mermaid
sequenceDiagram
  participant Fraud as fraud-ops-web
  box rgb(232, 240, 254) HashiBank Identity
    participant AppRole as AppRole
    participant SPIFFEMint as SPIFFE JWT minting
  end
  box rgb(234, 247, 238) HashiBank Access
    participant SPIFFEJWT as SPIFFE JWT auth
    participant DB as Database secrets
  end
  participant PG as Postgres

  Fraud->>AppRole: Login
  AppRole-->>Fraud: Vault token
  Fraud->>SPIFFEMint: Mint JWT-SVID
  SPIFFEMint-->>Fraud: JWT-SVID
  Fraud->>SPIFFEJWT: Authenticate with Authorization: Bearer <jwt>
  SPIFFEJWT-->>Fraud: Vault token
  Fraud->>DB: Read dynamic Postgres creds
  DB-->>Fraud: Short-lived DB username + password
  Fraud->>PG: Query fraud_alerts
```
---

## Slide 13 - Demo 3: Relationship assistant with OIDC validation

**Title:** Demo 3: `relationship-assistant` validates a Vault-minted SPIFFE JWT outside Vault

**On-slide content**

```mermaid
sequenceDiagram
  participant Assistant as relationship-assistant
  box rgb(232, 240, 254) HashiBank Identity
    participant AppRole as AppRole
    participant SPIFFEMint as SPIFFE JWT minting
    participant OIDC as Discovery + JWKS
  end
  participant RP as Assistant relying party

  Assistant->>AppRole: Login
  AppRole-->>Assistant: Vault token
  Assistant->>SPIFFEMint: Mint JWT-SVID
  SPIFFEMint-->>Assistant: JWT-SVID
  RP->>OIDC: Resolve discovery + keys
  Assistant->>RP: Present JWT-SVID
  RP-->>Assistant: Render masked banker context
```
