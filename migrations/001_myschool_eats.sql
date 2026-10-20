-- 1percent Eats PostgreSQL schema migration
-- Safe to run more than once. Back up the database before applying.
-- Existing schools and students tables must already exist.

CREATE TABLE IF NOT EXISTS eats_cards (
    id SERIAL PRIMARY KEY,
    school_id INTEGER NOT NULL REFERENCES schools(id),
    student_id INTEGER NOT NULL REFERENCES students(id),
    uid_hash VARCHAR(64) NOT NULL,
    balance_cents INTEGER NOT NULL DEFAULT 0 CHECK (balance_cents >= 0),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_eats_card_school_uid UNIQUE (school_id, uid_hash)
);

CREATE TABLE IF NOT EXISTS eats_items (
    id SERIAL PRIMARY KEY,
    school_id INTEGER NOT NULL REFERENCES schools(id),
    name VARCHAR(120) NOT NULL,
    description VARCHAR(300),
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    available INTEGER NOT NULL DEFAULT 1 CHECK (available IN (0, 1)),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS eats_transactions (
    id SERIAL PRIMARY KEY,
    school_id INTEGER NOT NULL REFERENCES schools(id),
    student_id INTEGER NOT NULL REFERENCES students(id),
    card_id INTEGER NOT NULL REFERENCES eats_cards(id),
    item_id INTEGER REFERENCES eats_items(id),
    transaction_type VARCHAR(20) NOT NULL DEFAULT 'purchase',
    amount_cents INTEGER NOT NULL,
    balance_after_cents INTEGER NOT NULL CHECK (balance_after_cents >= 0),
    status VARCHAR(20) NOT NULL DEFAULT 'approved',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_eats_cards_id ON eats_cards(id);
CREATE INDEX IF NOT EXISTS ix_eats_cards_school_id ON eats_cards(school_id);
CREATE INDEX IF NOT EXISTS ix_eats_cards_student_id ON eats_cards(student_id);
CREATE INDEX IF NOT EXISTS ix_eats_items_id ON eats_items(id);
CREATE INDEX IF NOT EXISTS ix_eats_items_school_id ON eats_items(school_id);
CREATE INDEX IF NOT EXISTS ix_eats_transactions_id ON eats_transactions(id);
CREATE INDEX IF NOT EXISTS ix_eats_transactions_school_id ON eats_transactions(school_id);
CREATE INDEX IF NOT EXISTS ix_eats_transactions_student_id ON eats_transactions(student_id);
CREATE INDEX IF NOT EXISTS ix_eats_transactions_card_id ON eats_transactions(card_id);
