"""
ClawPay — Mock Payment Gateway for ClawNexus.

A pluggable payment interface. The current implementation uses the
Supabase-backed vault for credits. Future modules can swap this for
Solana SPL tokens or Lightning Network payments.

Architecture:
  PaymentProvider (Abstract) → InternalCredits (current, Supabase)
                             → SolanaProvider   (Phase 5+)
"""

import os
from abc import ABC, abstractmethod
from dotenv import load_dotenv

from infrastructure import nexus_db as db

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))


# ============================================================
# Abstract Payment Provider (Pluggable Module Interface)
# ============================================================

class PaymentProvider(ABC):
    """Base class for all payment providers."""

    @abstractmethod
    def deposit_funds(self, agent_did: str, amount: float) -> dict:
        """Add funds to an agent's account."""
        ...

    @abstractmethod
    def withdraw_funds(self, agent_did: str, amount: float, destination: str = "") -> dict:
        """Withdraw funds from an agent's account."""
        ...

    @abstractmethod
    def get_balance(self, agent_did: str) -> float:
        """Check an agent's current balance."""
        ...

    @abstractmethod
    def get_provider_name(self) -> str:
        """Return the human-readable name of this provider."""
        ...


# ============================================================
# Internal Credits Provider (Current Default)
# ============================================================

class InternalCreditsProvider(PaymentProvider):
    """SQLite-backed credit system. Used for MVP and testing."""

    def deposit_funds(self, agent_did: str, amount: float) -> dict:
        if amount <= 0:
            return {"status": "error", "reason": "Amount must be positive"}
        db.update_agent_balance(agent_did, amount)
        db.log_transaction("DEPOSIT", agent_did, amount, details="via:InternalCredits")
        return {
            "status": "ok",
            "provider": self.get_provider_name(),
            "agent": agent_did,
            "deposited": amount,
            "balance": db.get_agent_balance(agent_did)
        }

    def withdraw_funds(self, agent_did: str, amount: float, destination: str = "") -> dict:
        if amount <= 0:
            return {"status": "error", "reason": "Amount must be positive"}
        balance = db.get_agent_balance(agent_did)
        if balance < amount:
            return {
                "status": "error",
                "reason": "Insufficient balance",
                "balance": balance,
                "requested": amount
            }
        db.update_agent_balance(agent_did, -amount)
        db.log_transaction("WITHDRAWAL", agent_did, amount, details=f"to:{destination or 'external'}")
        return {
            "status": "ok",
            "provider": self.get_provider_name(),
            "agent": agent_did,
            "withdrawn": amount,
            "balance": db.get_agent_balance(agent_did)
        }

    def get_balance(self, agent_did: str) -> float:
        return db.get_agent_balance(agent_did)

    def get_provider_name(self) -> str:
        return "InternalCredits"


# ============================================================
# Solana Provider Stub (Phase 5+)
# ============================================================

class SolanaProvider(PaymentProvider):
    """
    Placeholder for Solana SPL token payments.
    Will use solana-py and the wallet address from .env.
    """

    def __init__(self):
        self.wallet = os.getenv("SOLANA_WALLET_ADDRESS", "")

    def deposit_funds(self, agent_did: str, amount: float) -> dict:
        # TODO: Integrate with Solana RPC to verify on-chain deposit
        raise NotImplementedError("Solana provider coming in Phase 5")

    def withdraw_funds(self, agent_did: str, amount: float, destination: str = "") -> dict:
        # TODO: Send SPL tokens via Solana transaction
        raise NotImplementedError("Solana provider coming in Phase 5")

    def get_balance(self, agent_did: str) -> float:
        # TODO: Query on-chain balance
        raise NotImplementedError("Solana provider coming in Phase 5")

    def get_provider_name(self) -> str:
        return "Solana"


# ============================================================
# Factory — Get the Active Provider
# ============================================================

_active_provider = None


def get_payment_provider() -> PaymentProvider:
    """Return the configured payment provider (singleton)."""
    global _active_provider
    if _active_provider is None:
        provider_name = os.getenv("PAYMENT_PROVIDER", "internal").lower()
        if provider_name == "solana":
            _active_provider = SolanaProvider()
        else:
            _active_provider = InternalCreditsProvider()
    return _active_provider


# ============================================================
# Convenience Functions (for scripts and tests)
# ============================================================

def deposit_funds(agent_did: str, amount: float) -> dict:
    return get_payment_provider().deposit_funds(agent_did, amount)


def withdraw_funds(agent_did: str, amount: float, destination: str = "") -> dict:
    return get_payment_provider().withdraw_funds(agent_did, amount, destination)
