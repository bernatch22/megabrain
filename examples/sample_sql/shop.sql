-- ============================================================
-- SHOP SCHEMA — customers, orders, billing
-- ============================================================

-- Master customer record. Soft-deleted via `active`.
CREATE TABLE customers (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    email       TEXT UNIQUE,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- One row per order; status walks pending -> paid -> shipped.
CREATE TABLE orders (
    id           INTEGER PRIMARY KEY,
    customer_id  INTEGER NOT NULL REFERENCES customers(id),
    status       TEXT NOT NULL DEFAULT 'pending',
    total_cents  INTEGER NOT NULL,
    placed_at    TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_orders_customer ON orders(customer_id);

CREATE INDEX idx_orders_status ON orders(status);

-- ============================================================
-- BILLING
-- ============================================================

-- Invoices are generated from paid orders by the nightly job.
CREATE TABLE invoices (
    id           INTEGER PRIMARY KEY,
    order_id     INTEGER NOT NULL REFERENCES orders(id),
    amount_cents INTEGER NOT NULL,
    issued_at    TEXT DEFAULT CURRENT_TIMESTAMP,
    paid         INTEGER NOT NULL DEFAULT 0
);

-- Revenue per customer, only counting paid invoices.
CREATE VIEW customer_revenue AS
    SELECT c.id AS customer_id,
           c.name,
           SUM(i.amount_cents) AS revenue_cents
    FROM customers c
    JOIN orders o   ON o.customer_id = c.id
    JOIN invoices i ON i.order_id = o.id
    WHERE i.paid = 1
    GROUP BY c.id, c.name;

-- Seed data for local development.
INSERT INTO customers (name, email) VALUES
    ('Ada Lovelace',  'ada@example.com'),
    ('Alan Turing',   'alan@example.com'),
    ('Grace Hopper',  'grace@example.com');
