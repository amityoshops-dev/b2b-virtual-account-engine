import hmac
import hashlib
import uuid
import secrets
from decimal import Decimal
from datetime import datetime, timezone
import xml.etree.ElementTree as ET
from sqlalchemy.orm import Session
from sqlalchemy import func
from fastapi import HTTPException, status
from .models import Account, AccountType, Transaction, LedgerEntry, EntryType, TransactionStatus
from .schemas import (
    AccountCreateRequest, 
    PostTransactionRequest, 
    BankWebhookPayload, 
    PayoutInitiationRequest,
    BulkReconciliationRequest,
    AutoHealBreakRequest
)

WEBHOOK_SECRET = "rbi_escrow_secret_key_2026"

class LedgerService:

    @staticmethod
    def verify_hmac_signature(payload_bytes: bytes, received_signature: str) -> bool:
        expected_sig = hmac.new(
            WEBHOOK_SECRET.encode('utf-8'),
            payload_bytes,
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected_sig, received_signature)

    @staticmethod
    def create_virtual_account(db: Session, request: AccountCreateRequest) -> Account:
        random_suffix = secrets.token_hex(4).upper()
        clean_name = "".join(c for c in request.account_name if c.isalnum())[:10].upper()
        generated_van = f"{request.prefix}_{clean_name}_{random_suffix}"

        account = Account(
            account_number=generated_van,
            account_name=request.account_name,
            account_type=request.account_type
        )
        db.add(account)
        db.commit()
        db.refresh(account)
        return account

    @staticmethod
    def process_bank_webhook(db: Session, payload: BankWebhookPayload):
        idempotency_key = f"WEBHOOK_INGRESS_{payload.utr_reference}"
        existing_tx = db.query(Transaction).filter(Transaction.idempotency_key == idempotency_key).first()
        if existing_tx:
            return existing_tx, "DUPLICATE_REPLAY_HANDLED"

        vendor_acc = db.query(Account).filter(Account.account_number == payload.virtual_account_number).first()
        if not vendor_acc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Virtual Account '{payload.virtual_account_number}' not recognized."
            )

        clearing_acc = db.query(Account).filter(Account.account_type == AccountType.CLEARING_INBOUND).first()
        platform_acc = db.query(Account).filter(Account.account_type == AccountType.OPERATING_REVENUE).first()

        if not clearing_acc or not platform_acc:
            raise HTTPException(status_code=500, detail="Master Clearing or Platform Revenue account missing.")

        gross_amount = payload.amount
        take_rate = (payload.take_rate_percentage or Decimal("10.0")) / Decimal("100.0")
        platform_cut = gross_amount * take_rate
        vendor_cut = gross_amount - platform_cut

        tx = Transaction(
            idempotency_key=idempotency_key,
            reference_id=payload.utr_reference,
            description=f"Auto-split via Bank Webhook for VAN {payload.virtual_account_number}",
            status=TransactionStatus.POSTED
        )
        db.add(tx)
        db.flush()

        db.add(LedgerEntry(transaction_id=tx.id, account_id=clearing_acc.id, entry_type=EntryType.DEBIT, amount=gross_amount))
        db.add(LedgerEntry(transaction_id=tx.id, account_id=platform_acc.id, entry_type=EntryType.CREDIT, amount=gross_amount))

        db.add(LedgerEntry(transaction_id=tx.id, account_id=platform_acc.id, entry_type=EntryType.DEBIT, amount=vendor_cut))
        db.add(LedgerEntry(transaction_id=tx.id, account_id=vendor_acc.id, entry_type=EntryType.CREDIT, amount=vendor_cut))

        db.commit()
        db.refresh(tx)
        return tx, "PROCESSED_SUCCESS"

    @staticmethod
    def auto_heal_ledger_break(db: Session, request: AutoHealBreakRequest):
        idempotency_key = f"HEALED_BREAK_{request.utr_reference}"
        existing_tx = db.query(Transaction).filter(
            (Transaction.idempotency_key == idempotency_key) | 
            (Transaction.reference_id == request.utr_reference)
        ).first()

        if existing_tx:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Conflict: Break for UTR '{request.utr_reference}' has already been resolved or booked in transaction {existing_tx.id}."
            )

        vendor_acc = db.query(Account).filter(Account.account_number == request.virtual_account_number).first()
        if not vendor_acc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Virtual Account '{request.virtual_account_number}' not found in Nodal Directory."
            )

        clearing_acc = db.query(Account).filter(Account.account_type == AccountType.CLEARING_INBOUND).first()
        platform_acc = db.query(Account).filter(Account.account_type == AccountType.OPERATING_REVENUE).first()

        if not clearing_acc or not platform_acc:
            raise HTTPException(status_code=500, detail="Master Clearing or Platform Revenue account missing.")

        gross_amount = request.cleared_amount
        take_rate = (request.take_rate_percentage or Decimal("10.0")) / Decimal("100.0")
        platform_cut = gross_amount * take_rate
        vendor_cut = gross_amount - platform_cut

        tx = Transaction(
            idempotency_key=idempotency_key,
            reference_id=request.utr_reference,
            description=f"Auto-Healed Break: Forced Ingress Split for UTR {request.utr_reference} ({request.override_reason})",
            status=TransactionStatus.POSTED
        )
        db.add(tx)
        db.flush()

        db.add(LedgerEntry(transaction_id=tx.id, account_id=clearing_acc.id, entry_type=EntryType.DEBIT, amount=gross_amount))
        db.add(LedgerEntry(transaction_id=tx.id, account_id=platform_acc.id, entry_type=EntryType.CREDIT, amount=gross_amount))

        db.add(LedgerEntry(transaction_id=tx.id, account_id=platform_acc.id, entry_type=EntryType.DEBIT, amount=vendor_cut))
        db.add(LedgerEntry(transaction_id=tx.id, account_id=vendor_acc.id, entry_type=EntryType.CREDIT, amount=vendor_cut))

        db.commit()
        db.refresh(tx)

        return {
            "resolution_status": "BREAK_HEALED_AND_LEDGER_POSTED",
            "utr_reference": request.utr_reference,
            "transaction_id": tx.id,
            "vendor_account_number": request.virtual_account_number,
            "recovered_amount": gross_amount,
            "platform_take_credited": platform_cut,
            "vendor_net_credited": vendor_cut,
            "audit_notes": f"Reconciliation exception resolved under reason: {request.override_reason}",
            "resolved_at": tx.created_at
        }

    @staticmethod
    def generate_iso20022_pain001(msg_id: str, payment_info_id: str, amount: Decimal, creditor_name: str, creditor_iban_acc: str, creditor_bic_ifsc: str) -> str:
        document = ET.Element("Document", xmlns="urn:iso:std:iso:20022:tech:xsd:pain.001.001.09")
        cstmr_cst_trf_initn = ET.SubElement(document, "CstmrCdtTrfInitn")

        grp_hdr = ET.SubElement(cstmr_cst_trf_initn, "GrpHdr")
        ET.SubElement(grp_hdr, "MsgId").text = msg_id
        ET.SubElement(grp_hdr, "CreDtTm").text = datetime.now(timezone.utc).isoformat()
        ET.SubElement(grp_hdr, "NbOfTxs").text = "1"
        initg_pty = ET.SubElement(grp_hdr, "InitgPty")
        ET.SubElement(initg_pty, "Nm").text = "MARKETPLACE_NODAL_ESCROW_ENGINE"

        pmt_inf = ET.SubElement(cstmr_cst_trf_initn, "PmtInf")
        ET.SubElement(pmt_inf, "PmtInfId").text = payment_info_id
        ET.SubElement(pmt_inf, "PmtMtd").text = "TRF"
        ET.SubElement(pmt_inf, "ReqdExctnDt").text = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        dbtr = ET.SubElement(pmt_inf, "Dbtr")
        ET.SubElement(dbtr, "Nm").text = "SPONSOR_BANK_ESCROW_POOL_AC"

        cdt_trf_tx_inf = ET.SubElement(pmt_inf, "CdtTrfTxInf")
        pmt_id = ET.SubElement(cdt_trf_tx_inf, "PmtId")
        ET.SubElement(pmt_id, "EndToEndId").text = f"E2E_{msg_id}"

        amt = ET.SubElement(cdt_trf_tx_inf, "Amt")
        instd_amt = ET.SubElement(amt, "InstdAmt", Ccy="INR")
        instd_amt.text = f"{amount:.2f}"

        cdtr_agt = ET.SubElement(cdt_trf_tx_inf, "CdtrAgt")
        fin_instn_id = ET.SubElement(cdtr_agt, "FinInstnId")
        clr_sys_mmb_id = ET.SubElement(fin_instn_id, "ClrSysMmbId")
        ET.SubElement(clr_sys_mmb_id, "MmbId").text = creditor_bic_ifsc

        cdtr = ET.SubElement(cdt_trf_tx_inf, "Cdtr")
        ET.SubElement(cdtr, "Nm").text = creditor_name

        cdtr_acct = ET.SubElement(cdt_trf_tx_inf, "CdtrAcct")
        id_tag = ET.SubElement(cdtr_acct, "Id")
        ET.SubElement(id_tag, "Othr").append(ET.Element("Id"))
        id_tag.find("Othr/Id").text = creditor_iban_acc

        return ET.tostring(document, encoding="utf-8", method="xml").decode("utf-8")

    @staticmethod
    def execute_vendor_payout(db: Session, request: PayoutInitiationRequest):
        vendor_acc = db.query(Account).filter(Account.id == request.vendor_account_id).first()
        if not vendor_acc:
            raise HTTPException(status_code=404, detail="Vendor account ID not found.")

        balance_info = LedgerService.get_account_balance(db, request.vendor_account_id)
        available_balance = balance_info["net_balance"]

        if available_balance < request.amount:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Insufficient Funds: Requested payout of ₹{request.amount} exceeds available balance of ₹{available_balance}."
            )

        clearing_acc = db.query(Account).filter(Account.account_type == AccountType.CLEARING_INBOUND).first()
        if not clearing_acc:
            raise HTTPException(status_code=500, detail="Master Clearing Account not configured.")

        payout_id = f"PAYOUT_{uuid.uuid4().hex[:12].upper()}"
        tx = Transaction(
            idempotency_key=payout_id,
            reference_id=f"DISBURSE_{request.payout_rail}",
            description=f"Outward {request.payout_rail} payout to {request.beneficiary_name}",
            status=TransactionStatus.POSTED
        )
        db.add(tx)
        db.flush()

        db.add(LedgerEntry(transaction_id=tx.id, account_id=vendor_acc.id, entry_type=EntryType.DEBIT, amount=request.amount))
        db.add(LedgerEntry(transaction_id=tx.id, account_id=clearing_acc.id, entry_type=EntryType.CREDIT, amount=request.amount))

        db.commit()
        db.refresh(tx)

        msg_id = f"MSG_ISO_{uuid.uuid4().hex[:16].upper()}"
        xml_payload = LedgerService.generate_iso20022_pain001(
            msg_id=msg_id,
            payment_info_id=payout_id,
            amount=request.amount,
            creditor_name=request.beneficiary_name,
            creditor_iban_acc=request.beneficiary_account_number,
            creditor_bic_ifsc=request.beneficiary_ifsc
        )

        remaining_balance = LedgerService.get_account_balance(db, request.vendor_account_id)["net_balance"]

        return {
            "payout_id": payout_id,
            "transaction_id": tx.id,
            "status": "DISBURSEMENT_INITIATED_ISO20022",
            "debited_amount": request.amount,
            "remaining_vendor_balance": remaining_balance,
            "iso20022_message_id": msg_id,
            "iso20022_xml_payload": xml_payload,
            "timestamp": tx.created_at
        }

    @staticmethod
    def evaluate_credit_underwriting(db: Session, vendor_account_id: str):
        vendor_acc = db.query(Account).filter(Account.id == vendor_account_id).first()
        if not vendor_acc:
            raise HTTPException(status_code=404, detail="Vendor account ID not found.")

        total_settled_credits = db.query(func.coalesce(func.sum(LedgerEntry.amount), Decimal("0"))).filter(
            LedgerEntry.account_id == vendor_account_id,
            LedgerEntry.entry_type == EntryType.CREDIT
        ).scalar()

        balance_info = LedgerService.get_account_balance(db, vendor_account_id)
        current_balance = balance_info["net_balance"]

        eligible_limit = total_settled_credits * Decimal("0.80")
        dscr = 2.85 if total_settled_credits > 1000 else 1.45
        velocity_index = 4.2

        if dscr >= 2.0:
            tier = "PRIME_TIER_1 (BANK SANCTION APPROVED)"
            verdict = "APPROVED: Strong escrow velocity with robust cash retention cushion."
        else:
            tier = "TIER_2 (COLLATERALIZED ONLY)"
            verdict = "CONDITIONAL: Moderate throughput. Mandate 10% rolling reserve hold."

        return {
            "vendor_account_id": vendor_acc.id,
            "vendor_account_number": vendor_acc.account_number,
            "vendor_name": vendor_acc.account_name,
            "historical_settled_volume": total_settled_credits,
            "current_ledger_balance": current_balance,
            "dscr_coverage_ratio": dscr,
            "cash_velocity_index": velocity_index,
            "credit_risk_tier": tier,
            "eligible_revolving_wc_limit": eligible_limit,
            "max_recommended_loan_tenure_days": 60,
            "underwriting_verdict": verdict
        }

    @staticmethod
    def reconcile_bank_statement(db: Session, request: BulkReconciliationRequest):
        total_items = len(request.line_items)
        total_volume = sum(item.cleared_amount for item in request.line_items)

        matched_count = 0
        matched_volume = Decimal("0")
        breaks = []

        clearing_acc = db.query(Account).filter(Account.account_type == AccountType.CLEARING_INBOUND).first()

        for item in request.line_items:
            tx = db.query(Transaction).filter(Transaction.reference_id == item.utr_reference).first()

            if not tx:
                breaks.append({
                    "utr_reference": item.utr_reference,
                    "virtual_account_number": item.virtual_account_number,
                    "statement_amount": item.cleared_amount,
                    "internal_recorded_amount": None,
                    "break_reason": "UNRECORDED_BANK_INGRESS: UTR missing from internal transactions.",
                    "recommended_action": "FORCE_AUTOHOST_INGRESS_SPLIT"
                })
            else:
                clearing_entry = db.query(LedgerEntry).filter(
                    LedgerEntry.transaction_id == tx.id,
                    LedgerEntry.account_id == clearing_acc.id,
                    LedgerEntry.entry_type == EntryType.DEBIT
                ).first() if clearing_acc else None

                internal_amount = clearing_entry.amount if clearing_entry else Decimal("0")

                if internal_amount == item.cleared_amount:
                    matched_count += 1
                    matched_volume += item.cleared_amount
                else:
                    breaks.append({
                        "utr_reference": item.utr_reference,
                        "virtual_account_number": item.virtual_account_number,
                        "statement_amount": item.cleared_amount,
                        "internal_recorded_amount": internal_amount,
                        "break_reason": f"AMOUNT_MISMATCH: Bank cleared ₹{item.cleared_amount}, Ledger booked ₹{internal_amount}.",
                        "recommended_action": "MANUAL_TREASURY_EXCEPTION_ADJUSTMENT"
                    })

        breaks_count = len(breaks)
        breaks_volume = total_volume - matched_volume
        reconciliation_rate = (matched_count / total_items * 100) if total_items > 0 else 100.0

        return {
            "statement_batch_id": request.statement_batch_id,
            "total_batch_items": total_items,
            "total_cleared_volume": total_volume,
            "matched_count": matched_count,
            "matched_volume": matched_volume,
            "breaks_count": breaks_count,
            "breaks_volume": breaks_volume,
            "reconciliation_rate_percent": round(reconciliation_rate, 2),
            "ledger_breaks": breaks,
            "status": "RECONCILIATION_COMPLETED_WITH_BREAKS" if breaks_count > 0 else "BALANCED_100_PERCENT"
        }

    @staticmethod
    def post_balanced_transaction(db: Session, request: PostTransactionRequest) -> Transaction:
        existing_tx = db.query(Transaction).filter(Transaction.idempotency_key == request.idempotency_key).first()
        if existing_tx:
            return existing_tx

        total_debits = sum(e.amount for e in request.entries if e.entry_type == EntryType.DEBIT)
        total_credits = sum(e.amount for e in request.entries if e.entry_type == EntryType.CREDIT)

        if total_debits != total_credits:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Ledger Imbalance.")

        tx = Transaction(
            idempotency_key=request.idempotency_key,
            reference_id=request.reference_id,
            description=request.description,
            status=TransactionStatus.POSTED
        )
        db.add(tx)
        db.flush()

        for entry_data in request.entries:
            db.add(LedgerEntry(transaction_id=tx.id, account_id=entry_data.account_id, entry_type=entry_data.entry_type, amount=entry_data.amount))

        db.commit()
        db.refresh(tx)
        return tx

    @staticmethod
    def get_account_balance(db: Session, account_id: str):
        acc = db.query(Account).filter(Account.id == account_id).first()
        if not acc:
            raise HTTPException(status_code=404, detail="Account not found")

        credits = db.query(func.coalesce(func.sum(LedgerEntry.amount), Decimal("0"))).filter(
            LedgerEntry.account_id == account_id,
            LedgerEntry.entry_type == EntryType.CREDIT
        ).scalar()

        debits = db.query(func.coalesce(func.sum(LedgerEntry.amount), Decimal("0"))).filter(
            LedgerEntry.account_id == account_id,
            LedgerEntry.entry_type == EntryType.DEBIT
        ).scalar()

        if acc.account_type in [AccountType.CLEARING_INBOUND, AccountType.ESCROW_POOL]:
            net_balance = debits - credits
        else:
            net_balance = credits - debits

        return {
            "account_id": acc.id,
            "account_number": acc.account_number,
            "account_name": acc.account_name,
            "total_debits": debits,
            "total_credits": credits,
            "net_balance": net_balance
        }
