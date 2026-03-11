"""
Phase 4 QA Test — ClawNexus Economic Engine (Supabase Edition).

Full lifecycle verification: deposit → escrow → completion → fee audit.
Operates directly against the Supabase instance configured in .env.
"""

import os
import sys
import unittest

# Ensure we can import from the repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from infrastructure import nexus_db as db
from infrastructure.nexus_vault import (
    deposit, get_balance, lock_escrow, release_escrow,
    refund_escrow, get_platform_balance, calculate_fees, complete_mission
)
from core.claw_pay import deposit_funds, withdraw_funds, get_payment_provider

class TestEconomicEngine(unittest.TestCase):

    def setUp(self):
        """Reset DB tables between tests."""
        # Clean up existing test data (WARNING: this is destructive to the current Supabase DB!)
        db.supabase.table("transactions").delete().neq("tx_id", "dummy").execute()
        db.supabase.table("missions").delete().neq("mission_id", "dummy").execute()
        db.supabase.table("agents").delete().neq("did", "dummy").execute()
        
        # Reset treasury
        res = db.supabase.table("platform_treasury").select("*").eq("id", 1).execute()
        if not res.data:
            db.supabase.table("platform_treasury").insert({"id": 1, "balance": 0.0, "total_earned": 0.0}).execute()
        else:
            db.supabase.table("platform_treasury").update({"balance": 0.0, "total_earned": 0.0}).eq("id", 1).execute()

    # --- Fee Calculation ---
    def test_01_calculate_fees(self):
        """Test fee breakdown: 2% commission."""
        fees = calculate_fees(100.0)
        self.assertEqual(fees["gross"], 100.0)
        self.assertEqual(fees["commission"], 2.0)
        self.assertEqual(fees["net"], 98.0)
        # Micro-amount
        fees2 = calculate_fees(0.50)
        self.assertEqual(fees2["commission"], 0.01)
        self.assertEqual(fees2["net"], 0.49)
        print("[Test 1] PASSED — Fee calculation (2% of 100 = 2.0, 2% of 0.50 = 0.01)")

    # --- Deposit via Vault ---
    def test_02_deposit(self):
        """Deposit credits and verify balance."""
        result = deposit("did:clawnexus:sophia", 100.0)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["balance"], 100.0)
        # Second deposit
        result2 = deposit("did:clawnexus:sophia", 50.0)
        self.assertEqual(result2["balance"], 150.0)
        print("[Test 2] PASSED — Deposit and cumulative balance.")

    # --- Deposit via ClawPay ---
    def test_03_claw_pay_deposit(self):
        """Deposit through the payment gateway."""
        result = deposit_funds("did:clawnexus:kevin", 25.0)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["provider"], "InternalCredits")
        self.assertEqual(result["balance"], 25.0)
        print("[Test 3] PASSED — ClawPay deposit via InternalCredits provider.")

    # --- Escrow Lock with Commission ---
    def test_04_lock_escrow(self):
        """Lock funds in escrow, verify 2% commission deducted."""
        deposit("did:clawnexus:payer", 100.0)
        result = lock_escrow("mission_001", "did:clawnexus:payer", 10.0)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["gross_amount"], 10.0)
        self.assertEqual(result["commission"], 0.2)
        self.assertEqual(result["net_escrowed"], 9.8)
        self.assertEqual(result["remaining_balance"], 90.0)
        print("[Test 4] PASSED — Escrow lock with 2% commission.")

    # --- Platform Treasury ---
    def test_05_platform_treasury(self):
        """Verify platform treasury receives commission."""
        deposit("did:clawnexus:payer2", 100.0)
        lock_escrow("mission_002", "did:clawnexus:payer2", 50.0)
        treasury = get_platform_balance()
        self.assertEqual(treasury["balance"], 1.0)   # 2% of 50
        self.assertEqual(treasury["total_earned"], 1.0)
        print("[Test 5] PASSED — Platform treasury collected 1.0 credits.")

    # --- Release Escrow (Mission Complete) ---
    def test_06_complete_mission(self):
        """Full lifecycle: deposit → escrow → completion → mentor paid."""
        deposit("did:clawnexus:sophia_mentor", 100.0)
        lock_escrow("mission_003", "did:clawnexus:sophia_mentor", 20.0)
        result = complete_mission("mission_003", "did:clawnexus:kevin_mentor")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["amount"], 19.6)  # 20 - 0.4 commission
        mentor_balance = get_balance("did:clawnexus:kevin_mentor")
        self.assertEqual(mentor_balance, 19.6)
        print("[Test 6] PASSED — Mission completed, mentor received 19.6 credits.")

    # --- Refund Escrow ---
    def test_07_refund_escrow(self):
        """Refund escrow to payer, platform keeps commission."""
        deposit("did:clawnexus:payer3", 100.0)
        lock_escrow("mission_004", "did:clawnexus:payer3", 10.0)
        result = refund_escrow("mission_004")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["amount"], 9.8)
        self.assertEqual(result["commission_retained"], 0.2)
        payer_balance = get_balance("did:clawnexus:payer3")
        self.assertEqual(payer_balance, 99.8)  # 90 + 9.8
        print("[Test 7] PASSED — Refund issued, commission retained.")

    # --- Duplicate & Insufficient ---
    def test_08_insufficient_balance(self):
        """Reject escrow when balance too low."""
        deposit("did:clawnexus:broke", 5.0)
        result = lock_escrow("mission_005", "did:clawnexus:broke", 100.0)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "Insufficient balance")
        print("[Test 8] PASSED — Insufficient balance rejected.")

    def test_09_duplicate_escrow(self):
        """Cannot lock same mission twice."""
        deposit("did:clawnexus:dup", 100.0)
        lock_escrow("mission_006", "did:clawnexus:dup", 10.0)
        result = lock_escrow("mission_006", "did:clawnexus:dup", 10.0)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "Mission already in escrow")
        print("[Test 9] PASSED — Duplicate escrow rejected.")

    # --- Withdrawal via ClawPay ---
    def test_10_withdraw(self):
        """Withdraw credits through payment gateway."""
        deposit("did:clawnexus:withdrawer", 50.0)
        result = withdraw_funds("did:clawnexus:withdrawer", 20.0, "external_wallet")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["balance"], 30.0)
        # Overdraw
        result2 = withdraw_funds("did:clawnexus:withdrawer", 100.0)
        self.assertEqual(result2["status"], "error")
        self.assertEqual(result2["reason"], "Insufficient balance")
        print("[Test 10] PASSED — Withdrawal and overdraw protection.")

    # --- Transaction Audit Trail ---
    def test_11_audit_trail(self):
        """Verify immutable transaction log."""
        deposit("did:clawnexus:audited", 100.0)
        lock_escrow("mission_007", "did:clawnexus:audited", 25.0)
        complete_mission("mission_007", "did:clawnexus:mentor_a")
        txs = db.get_transactions("did:clawnexus:audited")
        types = [t["tx_type"] for t in txs]
        self.assertIn("DEPOSIT", types)
        self.assertIn("ESCROW_LOCK", types)
        # Check fee was logged
        lock_tx = [t for t in txs if t["tx_type"] == "ESCROW_LOCK"][0]
        self.assertEqual(lock_tx["fee_collected"], 0.5)  # 2% of 25
        print("[Test 11] PASSED — Immutable audit trail verified.")

    # --- Mission Lifecycle in DB ---
    def test_12_mission_lifecycle(self):
        """Verify mission status transitions in SQLite/Supabase."""
        deposit("did:clawnexus:lifecycle", 100.0)
        lock_escrow("mission_008", "did:clawnexus:lifecycle", 10.0)
        mission = db.get_mission("mission_008")
        self.assertEqual(mission["status"], "ESCROWED")
        complete_mission("mission_008", "did:clawnexus:mentor_b")
        mission = db.get_mission("mission_008")
        self.assertEqual(mission["status"], "COMPLETED")
        self.assertIsNotNone(mission["completed_at"])
        print("[Test 12] PASSED — Mission lifecycle: ESCROWED → COMPLETED.")


if __name__ == "__main__":
    print("=== Phase 4 Economic Engine QA Test Suite (Supabase) ===")
    unittest.main(verbosity=2)
