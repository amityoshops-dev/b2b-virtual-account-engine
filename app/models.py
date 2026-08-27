import enum
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Numeric, DateTime, ForeignKey, Enum, Text
from sqlalchemy.orm import relationship
from .database import Base

class AccountType(str, enum.Enum):
    ESCROW_POOL = "ESCROW_POOL"
    OPERATING_REVENUE = "OPERATING_REVENUE"
    VENDOR_VIRTUAL = "VENDOR_VIRTUAL"
    CLEARING_INBOUND = "CLEARING_INBOUND"
    RISK_RESERVE = "RISK_RESERVE"

class EntryType(str, enum.Enum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"

class TransactionStatus(str, enum.Enum):
    INITIATED = "INITIATED"
    POSTED = "POSTED"
    REVERSED = "REVERSED"

class Account(Base):
    __tablename__ = "accounts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    account_number = Column(String(64), unique=True, index=True, nullable=False)
    account_name = Column(String(128), nullable=False)
    account_type = Column(Enum(AccountType), nullable=False)
    currency = Column(String(3), default="INR", nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    entries = relationship("LedgerEntry", back_populates="account")

class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    idempotency_key = Column(String(128), unique=True, index=True, nullable=False)
    reference_id = Column(String(128), index=True, nullable=True)
    description = Column(Text, nullable=False)
    status = Column(Enum(TransactionStatus), default=TransactionStatus.INITIATED, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    entries = relationship("LedgerEntry", back_populates="transaction", cascade="all, delete-orphan")

class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    transaction_id = Column(String(36), ForeignKey("transactions.id"), nullable=False)
    account_id = Column(String(36), ForeignKey("accounts.id"), nullable=False)
    entry_type = Column(Enum(EntryType), nullable=False)
    amount = Column(Numeric(precision=18, scale=4), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    transaction = relationship("Transaction", back_populates="entries")
    account = relationship("Account", back_populates="entries")
