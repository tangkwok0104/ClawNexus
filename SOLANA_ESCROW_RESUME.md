# 🦞 ClawNexus Solana Escrow — Session Resume Doc

> **Last Updated:** 2026-03-11 20:45 AEST
> **Status:** Phase 8 COMPLETE ✅ — Deployed to Solana Mainnet
> **Contract Version:** v3 (security-hardened, events, auto-close)

---

## ✅ Completed Phases

### Phase 0: Toolchain Setup ✅
- Rust 1.94.0, Solana CLI v3.1.10, Anchor v0.32.1
- Devnet wallet configured at `~/.config/solana/devnet.json`

### Phase 1: Anchor Project ✅
- Scaffolded at `contracts/clawnexus_escrow/`
- `Anchor.toml` configured for Devnet cluster
- Dependencies installed via `npx yarn install`

### Phase 2: Smart Contract (v2 — bug-fixed) ✅ BUILD | ✅ DEPLOYED (Devnet)
- **File:** `contracts/clawnexus_escrow/programs/clawnexus_escrow/src/lib.rs`
- **4 Instructions:** create_escrow, release_escrow, refund_escrow, expire_escrow
- **Critical Bug Fix (v2):** Changed vault withdrawals from direct lamport manipulation to CPI `system_program::transfer` with `invoke_signed`

### Phase 3: Python SDK ✅
- **File:** `execution/solana_client.py`
- `ClawNexusOnChain` class — create/release/refund/expire + balance + get_escrow

### Phase 4: Discord Bot ✅ DEPLOYED
- `/nexus-wallet` → shows on-chain program ID, Explorer link, 3 security guarantees

### Phase 5: Guide Page ✅ DEPLOYED
- Updated ClawPay Step 4 → "On-Chain Safe" emphasis

### Phase 6: Integration Tests ✅ COMPLETE (8/8 happy-path)
### Phase 7: Adversarial Security Tests ✅ COMPLETE (8/8 adversarial)

### Phase 8: v3 Upgrade + Mainnet Deploy ✅ COMPLETE
- **v3 Improvements:**
  - ✅ Hardcoded treasury validation (`HCyBAE2rnqbcH87KTvwGGZ7EMZZHxxLGtVyQDtwFCEXC`)
  - ✅ PDA seeds include mentor (tighter isolation, anti-frontrun)
  - ✅ Escrow account auto-closing via `close = client` (rent reclaimed)
  - ✅ On-chain events: `EscrowCreated`, `EscrowReleased`, `EscrowRefunded`, `EscrowExpired`
  - ✅ Leaner state: removed `gross_amount`, `commission`, `platform_treasury`, `created_at`
  - ✅ Full-lamports vault transfer (no dust left behind)
  - ✅ Forward-reference fix: mentor declared before escrow_account in ReleaseEscrow
- **Mainnet Deploy TX:** `64aRKw9Lwt291fkZMHHhKzuqmiM3v4Dq17UtJe59HG5YXyQXSXV3J6MkojZKqwJi8MeYeSNRdMdtws1QgCMje1uH`
- **IDL Account:** `DJKR1X7wuBHLBCURrjGPXb9thggJDz49mN3NdeUG1TG6`

---

## 🔑 On-Chain Details

| Field | Value |
|-------|-------|
| **Program ID** | `tWrdP9vPV3j4DsJfdyWXdxLEZnRRLJuukkwHdmdipQv` |
| **Network** | **Solana Mainnet-Beta** ✅ |
| **Deployed Version** | v3 (security-hardened) ✅ |
| **Upgrade Authority** | `DDVzbaQ9JYHhBTgEQ3sNKdEJeFgm4vQE1vWf2MBkdk87` |
| **Treasury Wallet** | `HCyBAE2rnqbcH87KTvwGGZ7EMZZHxxLGtVyQDtwFCEXC` |
| **Explorer** | [View on Mainnet](https://explorer.solana.com/address/tWrdP9vPV3j4DsJfdyWXdxLEZnRRLJuukkwHdmdipQv) |
| **Mainnet Deploy TX** | `64aRKw9Lwt291fkZMHHhKzuqmiM3v4Dq17UtJe59HG5YXyQXSXV3J6MkojZKqwJi8MeYeSNRdMdtws1QgCMje1uH` |
| **Deploy Slot** | 405678691 |
| **Program Size** | 240,744 bytes (235 KB) |

---

## 💰 Wallets

| Wallet | Address | Keypair | Purpose |
|--------|---------|---------|---------|
| **Deploy** | `DDVzbaQ9JYHhBTgEQ3sNKdEJeFgm4vQE1vWf2MBkdk87` | `~/.config/solana/devnet.json` | Deploys & upgrades the contract |
| **Treasury** | `HCyBAE2rnqbcH87KTvwGGZ7EMZZHxxLGtVyQDtwFCEXC` | `~/.config/solana/clawnexus-treasury.json` | Receives 2% commission |

---

## 🛡️ Security Summary (v3)

| Attack Vector | Status | Defense |
|---|---|---|
| Fee theft | ✅ Impossible | Hardcoded `PLATFORM_TREASURY` + constraint |
| Unauthorized release/refund | ✅ Impossible | Signer + PDA seeds + client match |
| Wrong mentor paid | ✅ Impossible | `constraint = mentor.key() == escrow_account.mentor` |
| Funds permanently locked | ✅ Impossible | `expire_escrow` (permissionless crank) |
| Extra SOL dust in vault | ✅ Impossible | Uses `.lamports()` not stored `net_amount` |
| Reentrancy / PDA hijack | ✅ None | CPI to system_program only |
| Anti-frontrun | ✅ Seeds include mentor | No one can snipe mission_id |
| Treasury lock | ✅ Hardcoded | Only your wallet gets the 2% |

---

## 📁 Key Files

| File | Purpose |
|------|---------|
| `contracts/clawnexus_escrow/programs/clawnexus_escrow/src/lib.rs` | Rust smart contract (v3, mainnet) |
| `contracts/clawnexus_escrow/tests/clawnexus_escrow.ts` | Integration tests (16 cases) |
| `contracts/clawnexus_escrow/Anchor.toml` | Anchor config (mainnet) |
| `contracts/clawnexus_escrow/target/deploy/clawnexus_escrow.so` | Compiled binary |
| `execution/solana_client.py` | Python SDK for on-chain interactions |
| `~/.config/solana/devnet.json` | Deploy wallet keypair |
| `~/.config/solana/clawnexus-treasury.json` | Treasury wallet keypair |

---

## 🏗️ Architecture Summary

```
Discord User → /nexus-post → Discord Bot → Python SDK → Solana Program (MAINNET)
                                             (solana_client.py)  (lib.rs v3)
                                                   ↓                ↓
                                              Supabase          On-Chain
                                              (cache)         (source of truth)
                                                                   ↓
                                                          Treasury Wallet
                                                    (2% commission auto-collected)
```

**Key Design Decisions:**
- **Trustless:** Path B — no custodial escrow, everything on-chain
- **Supabase = cache**, blockchain = source of truth
- **2% flat commission** — deducted at escrow creation, non-refundable
- **PDA vaults** — deterministic, no key management
- **Private keys never stored on servers**
