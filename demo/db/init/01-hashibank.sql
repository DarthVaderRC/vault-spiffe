CREATE ROLE vaultadmin WITH LOGIN PASSWORD 'vaultadminpw' CREATEROLE;
GRANT CONNECT ON DATABASE hashibank TO vaultadmin;

\connect hashibank

CREATE TABLE IF NOT EXISTS fraud_alerts (
  id SERIAL PRIMARY KEY,
  account_mask TEXT NOT NULL,
  severity TEXT NOT NULL,
  status TEXT NOT NULL,
  amount NUMERIC(12,2) NOT NULL,
  merchant TEXT NOT NULL,
  event_time TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO fraud_alerts (account_mask, severity, status, amount, merchant, event_time)
VALUES
  ('**** 1042', 'HIGH', 'UNDER_REVIEW', 9284.11, 'Offshore Wire Exchange', NOW() - INTERVAL '12 minutes'),
  ('**** 7781', 'MEDIUM', 'NEW', 1240.00, 'High Velocity Card Not Present', NOW() - INTERVAL '34 minutes'),
  ('**** 2219', 'CRITICAL', 'ESCALATED', 18250.55, 'Cross-border Beneficiary Change', NOW() - INTERVAL '55 minutes'),
  ('**** 5510', 'LOW', 'WATCH', 245.12, 'New Device Retail Purchase', NOW() - INTERVAL '80 minutes'),
  ('**** 8893', 'HIGH', 'BLOCKED', 6400.00, 'After-hours ACH Modification', NOW() - INTERVAL '110 minutes');

GRANT USAGE, CREATE ON SCHEMA public TO vaultadmin;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO vaultadmin WITH GRANT OPTION;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO vaultadmin;
