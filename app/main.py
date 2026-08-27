from fastapi import FastAPI, Depends, Header, Request, status, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from typing import List
from decimal import Decimal
from .database import Base, engine, get_db
from .schemas import (
    AccountCreateRequest, AccountResponse, PostTransactionRequest, TransactionResponse,
    BalanceResponse, BankWebhookPayload, WebhookProcessedResponse, PayoutInitiationRequest,
    PayoutResponse, CreditUnderwritingResponse, BulkReconciliationRequest, BulkReconciliationReport,
    AutoHealBreakRequest, AutoHealBreakResponse, Camt053ReconciliationRequest
)
from .services import LedgerService
from .models import Account

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="B2B Virtual Account & Double-Entry Ledger Engine",
    description="Deterministic Transaction Banking Ledger API compliant with RBI Master Directions, Section 25 Ring-Fencing & ISO 20022 standards.",
    version="6.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

@app.get("/", include_in_schema=False)
def root_redirect(): return RedirectResponse(url="/docs")

@app.post("/api/v1/accounts/virtual", response_model=AccountResponse, tags=["Accounts & VAN"])
def create_virtual_account(request: AccountCreateRequest, db: Session = Depends(get_db)):
    return LedgerService.create_virtual_account(db, request)

@app.get("/api/v1/accounts", response_model=List[AccountResponse], tags=["Accounts & VAN"])
def list_accounts(db: Session = Depends(get_db)): return db.query(Account).all()

@app.post("/api/v1/webhooks/inbound-collection", response_model=WebhookProcessedResponse, tags=["Bank Webhook Ingress"])
async def handle_inbound_bank_webhook(payload: BankWebhookPayload, request: Request, x_bank_signature: str = Header(default="test_bypass_sig"), db: Session = Depends(get_db)):
    tx, status_msg = LedgerService.process_bank_webhook(db, payload)
    take = (payload.take_rate_percentage or Decimal("10.0")) / Decimal("100.0")
    return {"status": status_msg, "utr_reference": payload.utr_reference, "transaction_id": tx.id, "vendor_account_number": payload.virtual_account_number, "gross_collected": payload.amount, "platform_take_credited": payload.amount * take, "vendor_net_credited": payload.amount - (payload.amount * take), "timestamp": tx.created_at}

@app.post("/api/v1/payouts/disburse", response_model=PayoutResponse, tags=["Outward Payouts & ISO 20022"])
def initiate_vendor_payout(request: PayoutInitiationRequest, db: Session = Depends(get_db)): return LedgerService.execute_vendor_payout(db, request)

@app.get("/api/v1/underwriting/credit-assessment/{vendor_account_id}", response_model=CreditUnderwritingResponse, tags=["Bank Underwriting & Credit Risk"])
def evaluate_vendor_credit_risk(vendor_account_id: str, db: Session = Depends(get_db)): return LedgerService.evaluate_credit_underwriting(db, vendor_account_id)

@app.post("/api/v1/reconciliation/process-statement", response_model=BulkReconciliationReport, tags=["Reconciliation & Breaks Management"])
def reconcile_bank_statement(request: BulkReconciliationRequest, db: Session = Depends(get_db)): return LedgerService.reconcile_bank_statement(db, request)

@app.post("/api/v1/reconciliation/auto-heal-break", response_model=AutoHealBreakResponse, tags=["Reconciliation & Breaks Management"])
def auto_heal_ledger_break(request: AutoHealBreakRequest, db: Session = Depends(get_db)): return LedgerService.auto_heal_ledger_break(db, request)

@app.post("/api/v1/reconciliation/camt053-statement", response_model=BulkReconciliationReport, tags=["Reconciliation & Breaks Management"])
def reconcile_camt053_statement(request: Camt053ReconciliationRequest, db: Session = Depends(get_db)):
    """
    Ingests and parses a raw ISO 20022 camt.053 XML Bank Statement file payload.
    Automatically extracts <Ntry> blocks and executes ledger reconciliation.
    """
    return LedgerService.parse_camt053_and_reconcile(db, request)

@app.get("/health", tags=["System"])
def health_check(): return {"status": "HEALTHY", "engine": "B2B_VIRTUAL_ACCOUNT_LEDGER_V6_CAMT053_ACTIVE"}
