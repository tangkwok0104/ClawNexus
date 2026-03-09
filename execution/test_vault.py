"""
Phase 3 QA Test — Nexus Vault Escrow Logic.

Verifies deposit, lock, release, refund, insufficient balance,
and platform commission collection.
"""

import os
import sys
import json
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from nexus_vault import (
    deposit, get_balance, lock_escrow, release_escrow,
    refund_escrow, get_platform_balance, VAULT_FILE
)


class TestNexusVault(unittest.TestCase):

    def setUp(self):
        """Start each test with a clean vault."""
        if os.path.exists(VAULT_FILE):
            os.remove(VAULT_FILE)

    def tearDown(self):
        if os.path.exists(VAULT_FILE):
            os.remove(VAULT_FILE)

    def test_deposit(self):
        """Test 1: Deposit credits into an agent's wallet."""
        result = deposit("did:clawnexus:agent_a", 100.0)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["balance"], 100.0)
        # Second deposit
        result = deposit("did:clawnexus:agent_a", 50.0)
        self.assertEqual(result["balance"], 150.0)
        print("[Test 1] PASSED — Deposit works correctly.")

    def test_lock_escrow(self):
        """Test 2: Lock funds in escrow with 2% commission."""
        deposit("did:clawnexus:payer", 100.0)
        result = lock_escrow("mission_001", "did:clawnexus:payer", 10.0)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["gross_amount"], 10.0)
        self.assertEqual(result["commission"], 0.2)       # 2% of 10
        self.assertEqual(result["net_escrowed"], 9.8)      # 10 - 0.2
        self.assertEqual(result["remaining_balance"], 90.0)
        print("[Test 2] PASSED — Escrow lock with 2% commission.")

    def test_platform_commission(self):
        """Test 3: Platform wallet receives commission."""
        deposit("did:clawnexus:payer", 100.0)
        lock_escrow("mission_002", "did:clawnexus:payer", 50.0)
        platform = get_platform_balance()
        self.assertEqual(platform["balance"], 1.0)        # 2% of 50
        self.assertEqual(platform["total_earned"], 1.0)
        print("[Test 3] PASSED — Platform collected 2% commission.")

    def test_release_escrow(self):
        """Test 4: Release escrowed funds to payee (mentor)."""
        deposit("did:clawnexus:payer", 100.0)
        lock_escrow("mission_003", "did:clawnexus:payer", 20.0)
        result = release_escrow("mission_003", "did:clawnexus:mentor")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["amount"], 19.6)           # 20 - 0.4 commission
        mentor_balance = get_balance("did:clawnexus:mentor")
        self.assertEqual(mentor_balance, 19.6)
        print("[Test 4] PASSED — Escrow released to mentor.")

    def test_refund_escrow(self):
        """Test 5: Refund escrow to payer (commission retained)."""
        deposit("did:clawnexus:payer", 100.0)
        lock_escrow("mission_004", "did:clawnexus:payer", 10.0)
        result = refund_escrow("mission_004")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["amount"], 9.8)
        self.assertEqual(result["commission_retained"], 0.2)
        payer_balance = get_balance("did:clawnexus:payer")
        self.assertEqual(payer_balance, 99.8)              # 90 + 9.8
        print("[Test 5] PASSED — Escrow refunded, commission retained.")

    def test_insufficient_balance(self):
        """Test 6: Reject escrow when balance too low."""
        deposit("did:clawnexus:broke_agent", 5.0)
        result = lock_escrow("mission_005", "did:clawnexus:broke_agent", 100.0)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "Insufficient balance")
        print("[Test 6] PASSED — Insufficient balance rejected.")

    def test_duplicate_escrow(self):
        """Test 7: Cannot lock same mission twice."""
        deposit("did:clawnexus:payer", 100.0)
        lock_escrow("mission_006", "did:clawnexus:payer", 10.0)
        result = lock_escrow("mission_006", "did:clawnexus:payer", 10.0)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "Mission already in escrow")
        print("[Test 7] PASSED — Duplicate escrow rejected.")


if __name__ == "__main__":
    print("=== Phase 3 Vault QA Test Suite Start ===")
    unittest.main(verbosity=2)
