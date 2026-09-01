-- ============================================================================
-- B2B VIRTUAL ACCOUNT & DOUBLE-ENTRY LEDGER CORE SCHEMA
-- Regulatory Standard: RBI PSSA Section 25 Ring-Fencing & PA/PG Compliance
-- ============================================================================

-- Core Account Classifications
CREATE TYPE account_classification AS ENUM (
    'CLEARING_INBOUND',      -- Master Nodal / Escrow Inbound Transit Pool
    'VENDOR_VIRTUAL',        -- Merchant Individual Virtual Sub-Ledger (Payable)
    'OPERATING_REVENUE',     -- Platform Fee / Commission Sub-Ledger
    'OUTWARD_DISBURSEMENT'   -- Outbound Clearing Transit Account
);

CREATE TYPE entry_direction AS ENUM ('DEBIT', 'CREDIT');
CREATE TYPE transaction_state AS ENUM ('PENDING', 'SETTLED', 'FAILED', 'REVERSED');

-- 1. Accounts Table (Master and Sub-Ledgers)
CREATE TABLE IF NOT EXISTS accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_number VARCHAR(64) UNIQUE NOT NULL,
    account_name VARCHAR(128) NOT NULL,
    account_type account_classification NOT NULL,
    currency VARCHAR(3) DEFAULT 'INR' NOT NULL,
    is_active BOOLEAN DEFAULT TRUE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
);

-- 2. Transactions Table (Master Journal Header & Idempotency Key)
CREATE TABLE IF NOT EXISTS transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_ref VARCHAR(64) UNIQUE NOT NULL,
    utr_reference VARCHAR(64) UNIQUE NOT NULL, -- Database Idempotency Constraint
    source_channel VARCHAR(32) NOT NULL,        -- 'NPCI_UPI', 'RTGS_VAN', 'NACH_MANDATE', 'BBPS'
    gross_amount NUMERIC(18, 4) NOT NULL CHECK (gross_amount > 0),
    status transaction_state DEFAULT 'PENDING' NOT NULL,
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
);

-- 3. Ledger Entries Table (Immutable Double-Entry Lines: Debits == Credits)
CREATE TABLE IF NOT EXISTS ledger_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id UUID NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT,
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
    direction entry_direction NOT NULL,
    amount NUMERIC(18, 4) NOT NULL CHECK (amount > 0),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
);

-- 4. NPCI NACH Mandates Table
CREATE TABLE IF NOT EXISTS nach_mandates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    umrn VARCHAR(64) UNIQUE NOT NULL,
    vendor_account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
    max_amount NUMERIC(18, 4) NOT NULL CHECK (max_amount > 0),
    status VARCHAR(32) DEFAULT 'ACTIVE' NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
);

-- Indexing for Fast Sub-Ledger Balance Queries
CREATE INDEX IF NOT EXISTS idx_ledger_account_lookup ON ledger_entries(account_id, direction, amount);
CREATE INDEX IF NOT EXISTS idx_tx_utr ON transactions(utr_reference);
