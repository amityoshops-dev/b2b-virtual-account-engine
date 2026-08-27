# B2B Virtual Account & Double-Entry Ledger Engine

An institutional-grade Transaction Banking & Virtual Account Management (VAM) backend engine built with **FastAPI**, **SQLAlchemy**, and **PostgreSQL**. Designed in compliance with **RBI Master Directions on Payment Aggregators (PA-PG)** and **Section 25 of the Payment and Settlement Systems Act, 2007**.

---

## 🏛️ System Architecture & 4-Stage Lifecycle

\\\
[ Payer / Ingress ] ──▶ [ Sponsor Bank Webhook (HMAC-SHA256) ]
                               │
                               ▼
        [ Two-Legged Double-Entry Accounting Engine ]
        ├── Leg 1: Transit Inbound  ──▶  Platform Revenue A/C (Take-Rate)
        └── Leg 2: Net Realization ──▶  Vendor Virtual Sub-Account (VAN)
                               │
                               ▼
    ┌──────────────────────────┴──────────────────────────┐
    ▼                                                     ▼
[ ISO 20022 pain.001 Payout Egress ]     [ Working Capital Underwriting ]
- Real-time Balance Guards              - Historical Throughput Velocity
- XML Credit Transfer Initiation        - DSCR Coverage & Credit Lines
\\\

---

## 🚀 Key Functional Modules

* **Stage 1: Core Double-Entry Ledger Engine**
  * Enforces zero-sum mathematical parity: \SUM(DEBITS) == SUM(CREDITS)\.
  * Multi-tier accounts: Physical Escrow Pools, Operating Accounts, Inward Clearing, and Virtual Sub-Ledgers (VANs).
* **Stage 2: Bank Webhook Ingestion & Ingress Routing**
  * Ingests real-time bank collection webhooks with cryptographic **HMAC-SHA256** signature verification.
  * Idempotency guards prevent replay attacks on bank UTR references.
* **Stage 3: Outward Payouts & ISO 20022 XML Generator**
  * Initiates vendor payouts with real-time balance overdraft protection.
  * Dynamically formats outgoing settlement instructions into standardized **ISO 20022 \pain.001.001.09\** XML payloads.
* **Stage 4: Bank Working Capital & Credit Underwriting**
  * Analyzes historical cashflow throughput and cash velocity through virtual sub-ledgers.
  * Computes **Debt Service Coverage Ratio (DSCR)** and automates 60-day revolving credit line underwriting.

---

## 🛠️ API Reference Endpoints

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| \POST\ | \/api/v1/accounts/virtual\ | Creates a Virtual Account Number (VAN) mapped to an entity. |
| \GET\ | \/api/v1/accounts\ | Lists all pool, nodal, and virtual sub-accounts. |
| \POST\ | \/api/v1/webhooks/inbound-collection\ | Ingests bank credit webhooks with HMAC & auto-split logic. |
| \POST\ | \/api/v1/payouts/disburse\ | Debits vendor sub-ledger and generates ISO 20022 \pain.001\ XML. |
| \GET\ | \/api/v1/underwriting/credit-assessment/{id}\ | Computes DSCR, turnover velocity, and sanctioned credit limit. |
| \POST\ | \/api/v1/ledger/post-transaction\ | Direct balanced multi-party double-entry posting. |
| \GET\ | \/api/v1/ledger/balance/{id}\ | Computes real-time immutable balance statement. |

---

## 💻 Local Quickstart

\\\ash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the application
uvicorn app.main:app --reload --port 8000
\\\

Interactive Swagger documentation available locally at: \http://127.0.0.1:8000/docs\.

---

## 📜 Compliance & Regulatory Standards
* **RBI Section 25 Ring-Fencing:** Complete insulation of merchant sub-ledgers from platform balance sheets.
* **ISO 20022 Messaging:** Standardized international credit transfer message schemas (\pain.001\).
* **CERT-In Directives:** Immutable audit logging and cryptographic authentication standards.
