"""
ClawNexus Solana Client — Python SDK for the On-Chain Escrow Program.

Bridges the Python backend (nexus_vault.py, nexus_watchtower.py)
with the deployed Solana smart contract.

Usage:
    from solana_client import ClawNexusOnChain
    client = ClawNexusOnChain()
    result = client.create_escrow(mission_id, client_keypair, mentor_pubkey, 1.5, 72)
"""

import os
import hashlib
import logging
import asyncio
from typing import Optional, Tuple

from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.system_program import ID as SYSTEM_PROGRAM_ID
from solders.instruction import Instruction, AccountMeta
from solders.transaction import Transaction
from solders.message import Message
from solders.hash import Hash
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
import struct
import time
import base58

log = logging.getLogger("clawnexus.solana")

# --- Configuration ---
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
PROGRAM_ID = Pubkey.from_string(
    os.getenv("ESCROW_PROGRAM_ID", "tWrdP9vPV3j4DsJfdyWXdxLEZnRRLJuukkwHdmdipQv")
)

# Constants (must match lib.rs)
PLATFORM_COMMISSION_BPS = 200  # 2%
MIN_ESCROW_LAMPORTS = 10_000_000     # 0.01 SOL
MAX_ESCROW_LAMPORTS = 100_000_000_000  # 100 SOL
LAMPORTS_PER_SOL = 1_000_000_000


def sol_to_lamports(sol: float) -> int:
    """Convert SOL to lamports (1 SOL = 1e9 lamports)."""
    return int(sol * LAMPORTS_PER_SOL)


def lamports_to_sol(lamports: int) -> float:
    """Convert lamports to SOL."""
    return lamports / LAMPORTS_PER_SOL


def mission_id_to_bytes(mission_id: str) -> bytes:
    """Convert a string mission ID to a 32-byte hash."""
    return hashlib.sha256(mission_id.encode()).digest()


def find_escrow_pda(mission_id_bytes: bytes, client_pubkey: Pubkey) -> Tuple[Pubkey, int]:
    """Derive the escrow account PDA."""
    seeds = [b"escrow", mission_id_bytes, bytes(client_pubkey)]
    return Pubkey.find_program_address(seeds, PROGRAM_ID)


def find_vault_pda(mission_id_bytes: bytes, client_pubkey: Pubkey) -> Tuple[Pubkey, int]:
    """Derive the escrow vault PDA."""
    seeds = [b"vault", mission_id_bytes, bytes(client_pubkey)]
    return Pubkey.find_program_address(seeds, PROGRAM_ID)


def calculate_fees(amount_sol: float) -> dict:
    """Calculate the fee breakdown for an escrow amount."""
    gross_lamports = sol_to_lamports(amount_sol)
    commission = (gross_lamports * PLATFORM_COMMISSION_BPS) // 10_000
    net = gross_lamports - commission
    return {
        "gross_sol": amount_sol,
        "gross_lamports": gross_lamports,
        "commission_sol": lamports_to_sol(commission),
        "commission_lamports": commission,
        "net_sol": lamports_to_sol(net),
        "net_lamports": net,
    }


# ============================================================
# Anchor Instruction Discriminators
# (first 8 bytes of SHA256 of "global:<instruction_name>")
# ============================================================
def _anchor_discriminator(name: str) -> bytes:
    """Compute the Anchor instruction discriminator."""
    return hashlib.sha256(f"global:{name}".encode()).digest()[:8]


CREATE_ESCROW_DISC = _anchor_discriminator("create_escrow")
RELEASE_ESCROW_DISC = _anchor_discriminator("release_escrow")
REFUND_ESCROW_DISC = _anchor_discriminator("refund_escrow")
EXPIRE_ESCROW_DISC = _anchor_discriminator("expire_escrow")


# ============================================================
# Escrow Status Parsing
# ============================================================
STATUS_MAP = {0: "Funded", 1: "Completed", 2: "Refunded", 3: "Expired"}


def parse_escrow_account(data: bytes) -> dict:
    """Parse on-chain EscrowAccount data into a Python dict."""
    # Skip 8-byte Anchor discriminator
    d = data[8:]
    # Parse fields in order matching the Rust struct
    mission_id = d[0:32]
    client = Pubkey.from_bytes(d[32:64])
    mentor = Pubkey.from_bytes(d[64:96])
    gross_amount = struct.unpack_from("<Q", d, 96)[0]
    commission = struct.unpack_from("<Q", d, 104)[0]
    net_amount = struct.unpack_from("<Q", d, 112)[0]
    platform_treasury = Pubkey.from_bytes(d[120:152])
    status_byte = d[152]
    created_at = struct.unpack_from("<q", d, 153)[0]
    deadline = struct.unpack_from("<q", d, 161)[0]
    bump = d[169]
    vault_bump = d[170]

    return {
        "mission_id": mission_id.hex(),
        "client": str(client),
        "mentor": str(mentor),
        "gross_amount": gross_amount,
        "gross_sol": lamports_to_sol(gross_amount),
        "commission": commission,
        "commission_sol": lamports_to_sol(commission),
        "net_amount": net_amount,
        "net_sol": lamports_to_sol(net_amount),
        "platform_treasury": str(platform_treasury),
        "status": STATUS_MAP.get(status_byte, "Unknown"),
        "status_code": status_byte,
        "created_at": created_at,
        "deadline": deadline,
        "bump": bump,
        "vault_bump": vault_bump,
    }


# ============================================================
# Main Client Class
# ============================================================
class ClawNexusOnChain:
    """Python SDK for the ClawNexus on-chain escrow program."""

    def __init__(self, rpc_url: str = SOLANA_RPC_URL):
        self.rpc_url = rpc_url
        self.client = AsyncClient(rpc_url, commitment=Confirmed)

    async def get_balance(self, pubkey: Pubkey) -> float:
        """Get SOL balance for a public key."""
        resp = await self.client.get_balance(pubkey)
        return lamports_to_sol(resp.value)

    async def get_escrow(
        self, mission_id: str, client_pubkey: Pubkey
    ) -> Optional[dict]:
        """Fetch and parse an escrow account from on-chain."""
        mid_bytes = mission_id_to_bytes(mission_id)
        escrow_pda, _ = find_escrow_pda(mid_bytes, client_pubkey)

        resp = await self.client.get_account_info(escrow_pda)
        if resp.value is None:
            return None

        return parse_escrow_account(bytes(resp.value.data))

    async def create_escrow(
        self,
        mission_id: str,
        client_keypair: Keypair,
        mentor_pubkey: Pubkey,
        amount_sol: float,
        deadline_hours: int,
        platform_treasury: Pubkey,
    ) -> dict:
        """Create and fund an escrow on-chain."""
        amount_lamports = sol_to_lamports(amount_sol)
        deadline_ts = int(time.time()) + (deadline_hours * 3600)
        mid_bytes = mission_id_to_bytes(mission_id)

        escrow_pda, escrow_bump = find_escrow_pda(mid_bytes, client_keypair.pubkey())
        vault_pda, vault_bump = find_vault_pda(mid_bytes, client_keypair.pubkey())

        # Build instruction data: discriminator + mission_id(32) + amount(u64) + deadline(i64)
        ix_data = CREATE_ESCROW_DISC
        ix_data += mid_bytes  # 32 bytes
        ix_data += struct.pack("<Q", amount_lamports)  # u64
        ix_data += struct.pack("<q", deadline_ts)  # i64

        accounts = [
            AccountMeta(client_keypair.pubkey(), is_signer=True, is_writable=True),
            AccountMeta(mentor_pubkey, is_signer=False, is_writable=False),
            AccountMeta(escrow_pda, is_signer=False, is_writable=True),
            AccountMeta(vault_pda, is_signer=False, is_writable=True),
            AccountMeta(platform_treasury, is_signer=False, is_writable=True),
            AccountMeta(SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
        ]

        ix = Instruction(PROGRAM_ID, ix_data, accounts)
        blockhash_resp = await self.client.get_latest_blockhash()
        blockhash = blockhash_resp.value.blockhash

        msg = Message.new_with_blockhash([ix], client_keypair.pubkey(), blockhash)
        tx = Transaction.new_unsigned(msg)
        tx.sign([client_keypair], blockhash)

        resp = await self.client.send_transaction(tx)
        log.info(f"create_escrow TX: {resp.value}")

        fees = calculate_fees(amount_sol)
        return {
            "status": "ok",
            "tx_signature": str(resp.value),
            "escrow_pda": str(escrow_pda),
            "vault_pda": str(vault_pda),
            **fees,
        }

    async def release_escrow(
        self, mission_id: str, client_keypair: Keypair, mentor_pubkey: Pubkey
    ) -> dict:
        """Release escrow — pay the mentor."""
        mid_bytes = mission_id_to_bytes(mission_id)
        escrow_pda, _ = find_escrow_pda(mid_bytes, client_keypair.pubkey())
        vault_pda, _ = find_vault_pda(mid_bytes, client_keypair.pubkey())

        ix_data = RELEASE_ESCROW_DISC

        accounts = [
            AccountMeta(client_keypair.pubkey(), is_signer=True, is_writable=True),
            AccountMeta(mentor_pubkey, is_signer=False, is_writable=True),
            AccountMeta(escrow_pda, is_signer=False, is_writable=True),
            AccountMeta(vault_pda, is_signer=False, is_writable=True),
        ]

        ix = Instruction(PROGRAM_ID, ix_data, accounts)
        blockhash_resp = await self.client.get_latest_blockhash()
        blockhash = blockhash_resp.value.blockhash

        msg = Message.new_with_blockhash([ix], client_keypair.pubkey(), blockhash)
        tx = Transaction.new_unsigned(msg)
        tx.sign([client_keypair], blockhash)

        resp = await self.client.send_transaction(tx)
        log.info(f"release_escrow TX: {resp.value}")

        return {"status": "ok", "tx_signature": str(resp.value)}

    async def refund_escrow(
        self, mission_id: str, client_keypair: Keypair
    ) -> dict:
        """Refund escrow — return funds to client (minus commission)."""
        mid_bytes = mission_id_to_bytes(mission_id)
        escrow_pda, _ = find_escrow_pda(mid_bytes, client_keypair.pubkey())
        vault_pda, _ = find_vault_pda(mid_bytes, client_keypair.pubkey())

        ix_data = REFUND_ESCROW_DISC

        accounts = [
            AccountMeta(client_keypair.pubkey(), is_signer=True, is_writable=True),
            AccountMeta(escrow_pda, is_signer=False, is_writable=True),
            AccountMeta(vault_pda, is_signer=False, is_writable=True),
        ]

        ix = Instruction(PROGRAM_ID, ix_data, accounts)
        blockhash_resp = await self.client.get_latest_blockhash()
        blockhash = blockhash_resp.value.blockhash

        msg = Message.new_with_blockhash([ix], client_keypair.pubkey(), blockhash)
        tx = Transaction.new_unsigned(msg)
        tx.sign([client_keypair], blockhash)

        resp = await self.client.send_transaction(tx)
        log.info(f"refund_escrow TX: {resp.value}")

        return {"status": "ok", "tx_signature": str(resp.value)}

    async def expire_escrow(
        self,
        mission_id: str,
        caller_keypair: Keypair,
        client_pubkey: Pubkey,
    ) -> dict:
        """Expire escrow — permissionless auto-refund after deadline."""
        mid_bytes = mission_id_to_bytes(mission_id)
        escrow_pda, _ = find_escrow_pda(mid_bytes, client_pubkey)
        vault_pda, _ = find_vault_pda(mid_bytes, client_pubkey)

        ix_data = EXPIRE_ESCROW_DISC

        accounts = [
            AccountMeta(caller_keypair.pubkey(), is_signer=True, is_writable=False),
            AccountMeta(client_pubkey, is_signer=False, is_writable=True),
            AccountMeta(escrow_pda, is_signer=False, is_writable=True),
            AccountMeta(vault_pda, is_signer=False, is_writable=True),
        ]

        ix = Instruction(PROGRAM_ID, ix_data, accounts)
        blockhash_resp = await self.client.get_latest_blockhash()
        blockhash = blockhash_resp.value.blockhash

        msg = Message.new_with_blockhash([ix], caller_keypair.pubkey(), blockhash)
        tx = Transaction.new_unsigned(msg)
        tx.sign([caller_keypair], blockhash)

        resp = await self.client.send_transaction(tx)
        log.info(f"expire_escrow TX: {resp.value}")

        return {"status": "ok", "tx_signature": str(resp.value)}

    async def close(self):
        """Close the RPC connection."""
        await self.client.close()


# ============================================================
# Convenience: sync wrappers for use in non-async code
# ============================================================
def _run(coro):
    """Run an async function from synchronous code."""
    try:
        loop = asyncio.get_running_loop()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return loop.run_in_executor(pool, asyncio.run, coro)
    except RuntimeError:
        return asyncio.run(coro)


# Singleton for reuse
_client: Optional[ClawNexusOnChain] = None

def get_client() -> ClawNexusOnChain:
    global _client
    if _client is None:
        _client = ClawNexusOnChain()
    return _client
