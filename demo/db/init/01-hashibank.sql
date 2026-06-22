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

CREATE TABLE IF NOT EXISTS customer_relationships (
  id SERIAL PRIMARY KEY,
  customer_mask TEXT NOT NULL,
  segment TEXT NOT NULL,
  relationship_tier TEXT NOT NULL,
  lifetime_value NUMERIC(14,2) NOT NULL,
  primary_product TEXT NOT NULL,
  next_best_action TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO customer_relationships (customer_mask, segment, relationship_tier, lifetime_value, primary_product, next_best_action, updated_at)
VALUES
  ('**** 4417', 'PRIVATE_WEALTH', 'PLATINUM', 1840500.00, 'Discretionary Portfolio', 'Schedule annual wealth review', NOW() - INTERVAL '3 days'),
  ('**** 9920', 'SME_BANKING', 'GOLD', 412300.00, 'Working Capital Facility', 'Offer FX hedging consultation', NOW() - INTERVAL '9 days'),
  ('**** 1185', 'RETAIL_PREMIER', 'GOLD', 96750.00, 'Offset Home Loan', 'Review redraw and rate options', NOW() - INTERVAL '1 day'),
  ('**** 7762', 'PRIVATE_WEALTH', 'PLATINUM', 2675000.00, 'Structured Investment', 'Introduce estate planning desk', NOW() - INTERVAL '14 days'),
  ('**** 3308', 'RETAIL_PREMIER', 'SILVER', 38400.00, 'Everyday Plus Account', 'Promote savings goal automation', NOW() - INTERVAL '6 hours');

GRANT USAGE, CREATE ON SCHEMA public TO vaultadmin;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO vaultadmin WITH GRANT OPTION;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO vaultadmin;
