# B2B Virtual Account & Double-Entry Ledger Engine

An enterprise transaction banking infrastructure and treasury operations engine compliant with RBI Section 25 Nodal/Escrow regulations and ISO 20022 messaging standards.

## Live Endpoints
- **Operations Portal (Next.js):** [https://b2b-ledger-portal.vercel.app](https://b2b-ledger-portal.vercel.app)
- **Ledger API Docs (FastAPI):** [https://b2b-virtual-account-engine.onrender.com/docs](https://b2b-virtual-account-engine.onrender.com/docs)

## Key Architecture Pillars
1. **Deterministic Double-Entry Sub-Ledger:** Guarantees strict balance invariants (Debits = Credits) across all transactions.
2. **Virtual Account Number (VAN) Ingress:** Real-time webhook reconciliation and automated marketplace take-rate splits.
3. **ISO 20022 Payment Rails:**
   - **pain.001.001.09:** Outward credit transfer initialization generator.
   - **camt.053.001.08:** EOD bank statement XML ingestion and break detection.
4. **Automated Exception Healing:** 1-click idempotent ledger break recovery.
5. **Real-Time Credit Underwriting:** Dynamic DSCR scoring and cash velocity-based revolving credit lines.

## Tech Stack
- **Backend:** FastAPI, Python, SQLAlchemy, PostgreSQL (Neon Cloud)
- **Frontend:** Next.js (App Router), Tailwind CSS, Lucide Icons, Vercel
