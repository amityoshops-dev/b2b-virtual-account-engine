import os
import uuid
import datetime
from decimal import Decimal
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import (
    create_engine, Column, String, Numeric, DateTime, 
    ForeignKey, func, desc, text
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship
import xml.etree.ElementTree as ET

# Database Configuration
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./ledger.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- Models ---
class Account(Base):
    __tablename__ = "accounts"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    account_number = Column(String, unique=True, index=True, nullable=False)
    account_name = Column(String, nullable=False)
    account_type = Column(String, nullable=False)  # CLEARING_INBOUND, VENDOR_VIRTUAL, OPERATING_REVENUE, OUTWARD_DISBURSEMENT
    currency = Column(String, default="INR")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    ledger_entries = relationship("LedgerEntry", back_populates="account")

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    transaction_ref = Column(String, unique=True, index=True, nullable=False)
    utr_reference = Column(String, unique=True, index=True, nullable=False)
    source_channel = Column(String, default="NPCI_IMPS")
    amount = Column(Numeric(precision=18, scale=2), nullable=False)
    status = Column(String, default="SETTLED")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    ledger_entries = relationship("LedgerEntry", back_populates="transaction")

class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    transaction_id = Column(String, ForeignKey("transactions.id"), nullable=False)
    account_id = Column(String, ForeignKey("accounts.id"), nullable=False)
    entry_type = Column(String, nullable=False)  # DEBIT or CREDIT
    amount = Column(Numeric(precision=18, scale=2), nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    transaction = relationship("Transaction", back_populates="ledger_entries")
    account = relationship("Account", back_populates="ledger_entries")

class NachMandate(Base):
    __tablename__ = "nach_mandates"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    umrn = Column(String, unique=True, index=True, nullable=False)
    vendor_account_id = Column(String, ForeignKey("accounts.id"), nullable=False)
    max_amount = Column(Numeric(precision=18, scale=2), nullable=False)
    status = Column(String, default="ACTIVE")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="B2B Virtual Account & Double-Entry Ledger Engine", version="7.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- Schemas ---
class InboundCollectionPayload(BaseModel):
    event: str
    utr_reference: str
    virtual_account_number: str
    amount: float = Field(gt=0)
    payer_vpa: Optional[str] = "corporate@okhdfcbank"
    take_rate_percentage: float = 10.0

class OutwardPayoutPayload(BaseModel):
    vendor_account_id: str
    amount: float = Field(gt=0)
    beneficiary_name: str
    beneficiary_account_number: str
    beneficiary_ifsc: str
    payout_rail: str = "IMPS"

class CamtStatementPayload(BaseModel):
    statement_batch_id: str
    xml_payload: str

class AutoHealPayload(BaseModel):
    utr_reference: str
    virtual_account_number: str
    cleared_amount: float
    take_rate_percentage: float = 10.0
    override_reason: str = "MANUAL_1_CLICK_HEAL"

class NachMandatePayload(BaseModel):
    vendor_account_id: str
    max_amount: float
    frequency: str = "MONTHLY"

# --- Ingress Helper ---
def execute_double_entry_collection(db: Session, utr: str, van: str, amount: float, take_rate: float, source: str = "UPI"):
    vendor = db.query(Account).filter(Account.account_number == van).first()
    if not vendor:
        raise HTTPException(status_code=404, detail=f"Vendor virtual account {van} not found.")

    pool = db.query(Account).filter(Account.account_type == "CLEARING_INBOUND").first()
    platform = db.query(Account).filter(Account.account_type == "OPERATING_REVENUE").first()

    total_amt = Decimal(str(amount))
    platform_cut = (total_amt * Decimal(str(take_rate))) / Decimal("100.00")
    vendor_cut = total_amt - platform_cut

    tx = Transaction(
        transaction_ref=f"TX_{uuid.uuid4().hex[:12].upper()}",
        utr_reference=utr,
        source_channel=source,
        amount=total_amt,
        status="SETTLED"
    )
    db.add(tx)
    db.flush()

    # Balanced entries (Sum Debits = Sum Credits)
    entries = [
        LedgerEntry(transaction_id=tx.id, account_id=pool.id, entry_type="DEBIT", amount=total_amt),
        LedgerEntry(transaction_id=tx.id, account_id=vendor.id, entry_type="CREDIT", amount=vendor_cut),
        LedgerEntry(transaction_id=tx.id, account_id=platform.id, entry_type="CREDIT", amount=platform_cut)
    ]
    db.add_all(entries)
    db.commit()
    return tx

# --- API Endpoints ---
@app.get("/api/v1/accounts")
def list_accounts(db: Session = Depends(get_db)):
    accs = db.query(Account).all()
    # If DB is empty, auto-seed
    if len(accs) == 0:
        seed_db(db)
        accs = db.query(Account).all()
    return accs

@app.post("/api/v1/webhooks/inbound-collection")
def handle_inbound(payload: InboundCollectionPayload, db: Session = Depends(get_db)):
    existing = db.query(Transaction).filter(Transaction.utr_reference == payload.utr_reference).first()
    if existing:
        return {"status": "ALREADY_PROCESSED", "transaction_ref": existing.transaction_ref}
    tx = execute_double_entry_collection(
        db, payload.utr_reference, payload.virtual_account_number, 
        payload.amount, payload.take_rate_percentage, source="NPCI_UPI"
    )
    return {"status": "SETTLED", "transaction_ref": tx.transaction_ref}

@app.get("/api/v1/underwriting/credit-assessment/{vendor_account_id}")
def get_underwriting(vendor_account_id: str, db: Session = Depends(get_db)):
    vendor = db.query(Account).filter(Account.id == vendor_account_id).first()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor account not found.")

    # Calculate real settled volume from ledger
    credits = db.query(func.coalesce(func.sum(LedgerEntry.amount), 0))\
        .filter(LedgerEntry.account_id == vendor_account_id, LedgerEntry.entry_type == "CREDIT").scalar()
    debits = db.query(func.coalesce(func.sum(LedgerEntry.amount), 0))\
        .filter(LedgerEntry.account_id == vendor_account_id, LedgerEntry.entry_type == "DEBIT").scalar()
    
    current_balance = Decimal(str(credits)) - Decimal(str(debits))
    settled_volume = Decimal(str(credits))

    # Dynamic metrics based on actual ledger history
    if settled_volume > 0:
        dscr = float(round(Decimal("1.8") + (settled_volume / Decimal("50000.0")), 2))
        velocity = float(round(Decimal("2.5") + (settled_volume / Decimal("25000.0")), 2))
        # Dynamic revolver limit formula: 50% of settled volume * DSCR scaling
        revolver_limit = float(round(settled_volume * Decimal("0.65") * Decimal(str(dscr / 2.0)), 2))
        tier = "TIER_1" if dscr >= 1.5 else "TIER_2"
        verdict = "APPROVED" if dscr >= 1.5 else "REVIEW_REQUIRED"
    else:
        dscr = 1.2
        velocity = 1.0
        revolver_limit = 50000.0
        tier = "TIER_2"
        verdict = "INITIAL_EVALUATION"

    return {
        "vendor_account_id": vendor.id,
        "vendor_account_number": vendor.account_number,
        "vendor_name": vendor.account_name,
        "historical_settled_volume": str(settled_volume),
        "current_ledger_balance": str(current_balance),
        "dscr_coverage_ratio": min(dscr, 8.5),
        "cash_velocity_index": min(velocity, 9.2),
        "credit_risk_tier": tier,
        "eligible_revolving_wc_limit": str(revolver_limit),
        "max_recommended_loan_tenure_days": 60 if tier == "TIER_1" else 30,
        "underwriting_verdict": verdict
    }

@app.post("/api/v1/payouts/disburse")
def disburse_payout(payload: OutwardPayoutPayload, db: Session = Depends(get_db)):
    vendor = db.query(Account).filter(Account.id == payload.vendor_account_id).first()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor account not found.")

    outward_pool = db.query(Account).filter(Account.account_type == "CLEARING_INBOUND").first()
    amt = Decimal(str(payload.amount))

    tx = Transaction(
        transaction_ref=f"TX_OUT_{uuid.uuid4().hex[:10].upper()}",
        utr_reference=f"UTR_OUT_{uuid.uuid4().hex[:8].upper()}",
        source_channel="ISO20022_PAIN001",
        amount=amt,
        status="DISBURSED"
    )
    db.add(tx)
    db.flush()

    entries = [
        LedgerEntry(transaction_id=tx.id, account_id=vendor.id, entry_type="DEBIT", amount=amt),
        LedgerEntry(transaction_id=tx.id, account_id=outward_pool.id, entry_type="CREDIT", amount=amt)
    ]
    db.add_all(entries)
    db.commit()

    msg_id = f"MSG_{uuid.uuid4().hex[:12].upper()}"
    pain_xml = f"""<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pain.001.001.09">
  <CstmrCdtTrfInitn>
    <GrpHdr><MsgId>{msg_id}</MsgId><CreDtTm>{datetime.datetime.utcnow().isoformat()}</CreDtTm><NbOfTxs>1</NbOfTxs></GrpHdr>
    <PmtInf>
      <PmtInfId>PMT_{tx.transaction_ref}</PmtInfId>
      <PmtMtd>TRF</PmtMtd>
      <DbtrAcct><Id><Othr><Id>{vendor.account_number}</Id></Othr></Id></DbtrAcct>
      <CdtTrfTxInf>
        <Amt><InstdAmt Ccy="INR">{payload.amount:.2f}</InstdAmt></Amt>
        <Cdtr><Nm>{payload.beneficiary_name}</Nm></Cdtr>
        <CdtrAcct><Id><Othr><Id>{payload.beneficiary_account_number}</Id></Othr></Id></CdtrAcct>
        <CdtrAgt><FinInstnId><ClrSysMmbId><MmbId>{payload.beneficiary_ifsc}</MmbId></ClrSysMmbId></FinInstnId></CdtrAgt>
      </CdtTrfTxInf>
    </PmtInf>
  </CstmrCdtTrfInitn>
</Document>"""

    return {
        "status": "DISBURSED",
        "transaction_ref": tx.transaction_ref,
        "iso20022_message_id": msg_id,
        "iso20022_xml_payload": pain_xml
    }

@app.post("/api/v1/reconciliation/camt053-statement")
def recon_camt053(payload: CamtStatementPayload, db: Session = Depends(get_db)):
    breaks = []
    total_entries = 0
    matched_entries = 0

    try:
        root = ET.fromstring(payload.xml_payload)
        namespaces = {'ns': 'urn:iso:std:iso:20022:tech:xsd:camt.053.001.08'}
        ntry_nodes = root.findall('.//ns:Ntry', namespaces) or root.findall('.//Ntry')

        for ntry in ntry_nodes:
            total_entries += 1
            amt_text = ntry.findtext('.//ns:Amt', '', namespaces) or ntry.findtext('.//Amt', '0.0')
            utr_text = ntry.findtext('.//ns:EndToEndId', '', namespaces) or ntry.findtext('.//EndToEndId', 'UNKNOWN')
            van_text = ntry.findtext('.//ns:CdtrAcct/ns:Id/ns:Othr/ns:Id', '', namespaces) or ntry.findtext('.//CdtrAcct/Id/Othr/Id', '')

            tx = db.query(Transaction).filter(Transaction.utr_reference == utr_text).first()
            if tx:
                matched_entries += 1
            else:
                breaks.append({
                    "utr_reference": utr_text,
                    "statement_amount": float(amt_text),
                    "virtual_account_number": van_text,
                    "break_reason": "UNMATCHED_INWARD_SETTLEMENT"
                })

        rate = (matched_entries / total_entries * 100.0) if total_entries > 0 else 100.0
        return {
            "statement_batch_id": payload.statement_batch_id,
            "total_records": total_entries,
            "matched_records": matched_entries,
            "breaks_count": len(breaks),
            "reconciliation_rate_percent": round(rate, 2),
            "ledger_breaks": breaks
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid CAMT.053 XML: {str(e)}")

@app.post("/api/v1/reconciliation/auto-heal-break")
def auto_heal(payload: AutoHealPayload, db: Session = Depends(get_db)):
    tx = execute_double_entry_collection(
        db, payload.utr_reference, payload.virtual_account_number, 
        payload.cleared_amount, payload.take_rate_percentage, source="CAMT053_AUTO_HEAL"
    )
    return {"status": "HEALED", "transaction_ref": tx.transaction_ref}

@app.post("/api/v1/npci/nach-mandate")
def create_nach(payload: NachMandatePayload, db: Session = Depends(get_db)):
    umrn = f"UMRN{uuid.uuid4().hex[:12].upper()}IN01"
    mandate = NachMandate(
        umrn=umrn,
        vendor_account_id=payload.vendor_account_id,
        max_amount=Decimal(str(payload.max_amount)),
        status="ACTIVE_REGISTERED"
    )
    db.add(mandate)
    db.commit()
    return {
        "umrn": umrn,
        "status": "ACTIVE_MANDATE_REGISTERED",
        "clearing_switch": "NPCI_NACH",
        "max_amount": payload.max_amount
    }

@app.post("/api/v1/seed-live-volume/{vendor_id}")
def seed_live_volume(vendor_id: str, db: Session = Depends(get_db)):
    vendor = db.query(Account).filter(Account.id == vendor_id).first()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    
    # Inject 5 realistic transactions to dynamically scale the DSCR and loan limits
    for i in range(5):
        execute_double_entry_collection(
            db, f"UTR_VOLUME_{uuid.uuid4().hex[:8].upper()}", 
            vendor.account_number, 50000.0, 10.0, source="NPCI_BULK_SETTLE"
        )
    return {"status": "INJECTED_VOLUME", "vendor_account": vendor.account_number}

def seed_db(db: Session):
    pool = Account(account_number="HDFC_NODAL_POOL_01", account_name="Inward_Clearing_Pool", account_type="CLEARING_INBOUND")
    platform = Account(account_number="HDFC_PLATFORM_FEE_01", account_name="Platform_Operating_Revenue", account_type="OPERATING_REVENUE")
    db.add_all([pool, platform])
    db.flush()

    for name in ["Alpha_Retail_Enterprises", "Beta_Electronics_Pvt", "Gamma_Logistics_Hub", "Delta_Pharma_Distributors"]:
        van = f"HDFC_VAN_{uuid.uuid4().hex[:8].upper()}"
        vendor = Account(account_number=van, account_name=f"Vendor_{name}", account_type="VENDOR_VIRTUAL")
        db.add(vendor)
        db.flush()
        # Seed an initial volume
        execute_double_entry_collection(db, f"UTR_INIT_{uuid.uuid4().hex[:6].upper()}", van, 75000.0, 10.0, source="INITIAL_CMS_DEPOSIT")
    
    db.commit()
