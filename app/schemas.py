from pydantic import BaseModel, Field
from typing import List, Optional
from decimal import Decimal
from datetime import datetime
from .models import AccountType, EntryType, TransactionStatus

class AccountCreateRequest(BaseModel):
    account_name: str = Field(..., example="Vendor_Alpha_Electronics")
    account_type: AccountType = Field(..., example=AccountType.VENDOR_VIRTUAL)
    prefix: Optional[str] = Field("HDFC", example="HDFC")

class AccountResponse(BaseModel):
    id: str
    account_number: str
    account_name: str
    account_type: AccountType
    currency: str
    created_at: datetime

    class Config:
        from_attributes = True

class EntryInstruction(BaseModel):
    account_id: str
    entry_type: EntryType
    amount: Decimal = Field(..., gt=0, decimal_places=2)

class PostTransactionRequest(BaseModel):
    idempotency_key: str = Field(..., example="ORD_2026_TX_1001")
    reference_id: Optional[str] = Field(None, example="UTR_AXIS_99482103")
    description: str = Field(..., example="Two-legged split for Order #1001")
    entries: List[EntryInstruction]

class TransactionResponse(BaseModel):
    transaction_id: str
    idempotency_key: str
    reference_id: Optional[str]
    status: TransactionStatus
    total_balanced_amount: Decimal
    created_at: datetime

class BalanceResponse(BaseModel):
    account_id: str
    account_number: str
    account_name: str
    total_debits: Decimal
    total_credits: Decimal
    net_balance: Decimal

class BankWebhookPayload(BaseModel):
    event: str = Field("collection.success", example="collection.success")
    utr_reference: str = Field(..., example="UTR_ICICI_2026_88921")
    virtual_account_number: str = Field(..., example="HDFC_VENDORALPH_5D295A4F")
    amount: Decimal = Field(..., gt=0, decimal_places=2, example=2500.00)
    payer_vpa: str = Field("customer@okhdfcbank", example="customer@okhdfcbank")
    take_rate_percentage: Optional[Decimal] = Field(Decimal("10.0"), example=10.0)

class WebhookProcessedResponse(BaseModel):
    status: str
    utr_reference: str
    transaction_id: str
    vendor_account_number: str
    gross_collected: Decimal
    platform_take_credited: Decimal
    vendor_net_credited: Decimal
    timestamp: datetime

class PayoutInitiationRequest(BaseModel):
    vendor_account_id: str = Field(..., example="PASTE_VENDOR_ACCOUNT_ID")
    amount: Decimal = Field(..., gt=0, decimal_places=2, example=1500.00)
    beneficiary_name: str = Field(..., example="Alpha Enterprises Pvt Ltd")
    beneficiary_account_number: str = Field(..., example="50100482910291")
    beneficiary_ifsc: str = Field(..., example="HDFC0000060")
    payout_rail: str = Field("IMPS", example="IMPS")

class PayoutResponse(BaseModel):
    payout_id: str
    transaction_id: str
    status: str
    debited_amount: Decimal
    remaining_vendor_balance: Decimal
    iso20022_message_id: str
    iso20022_xml_payload: str
    timestamp: datetime

class CreditUnderwritingResponse(BaseModel):
    vendor_account_id: str
    vendor_account_number: str
    vendor_name: str
    historical_settled_volume: Decimal
    current_ledger_balance: Decimal
    dscr_coverage_ratio: float
    cash_velocity_index: float
    credit_risk_tier: str
    eligible_revolving_wc_limit: Decimal
    max_recommended_loan_tenure_days: int
    underwriting_verdict: str

# STAGE 5 SCHEMAS: Bulk Statement & Reconciliation
class BankStatementLineItem(BaseModel):
    utr_reference: str = Field(..., example="UTR_ICICI_2026_88921")
    virtual_account_number: str = Field(..., example="HDFC_VENDORALPH_5D295A4F")
    cleared_amount: Decimal = Field(..., gt=0, decimal_places=2, example=2500.00)
    bank_timestamp: str = Field(..., example="2026-08-27T10:00:00Z")

class BulkReconciliationRequest(BaseModel):
    statement_batch_id: str = Field(..., example="BATCH_EOD_20260827_HDFC")
    clearing_cycle: str = Field("T_PLUS_1", example="T_PLUS_1")
    line_items: List[BankStatementLineItem]

class ReconciliationBreakItem(BaseModel):
    utr_reference: str
    virtual_account_number: str
    statement_amount: Decimal
    internal_recorded_amount: Optional[Decimal]
    break_reason: str
    recommended_action: str

class BulkReconciliationReport(BaseModel):
    statement_batch_id: str
    total_batch_items: int
    total_cleared_volume: Decimal
    matched_count: int
    matched_volume: Decimal
    breaks_count: int
    breaks_volume: Decimal
    reconciliation_rate_percent: float
    ledger_breaks: List[ReconciliationBreakItem]
    status: str
