# ClawNexus Modules — Plugin Developer Guide

## Overview

ClawNexus uses an **Open Core + Modules** architecture. The `core/` directory contains the auditable, sovereignty-grade protocol. The `modules/` directory is the plugin playground where anyone can extend the platform.

## Directory Structure

```
modules/
├── __init__.py
├── founder_vibe/          ← Anson's custom features (ships with repo)
│   ├── __init__.py
│   ├── nexus_watchtower.py
│   ├── nexus_web.py
│   ├── nexus_registry.py
│   ├── nexus_market.py
│   └── translations.py
└── your_module/           ← Your custom plugin goes here
    ├── __init__.py
    └── ...
```

## Creating a New Module

### 1. Create a folder in `/modules/`

```bash
mkdir modules/my_awesome_plugin
```

### 2. Add an `__init__.py` with metadata

```python
"""My Awesome Plugin — Extends ClawNexus with X."""

MODULE_NAME = "my_awesome_plugin"
MODULE_VERSION = "0.1.0"
MODULE_AUTHOR = "Your Name"
MODULE_DESCRIPTION = "Short description of what this module does."
```

The `nexus_kernel.py` boot loader will auto-discover your module and include it in the boot report.

### 3. Import from Core & Infrastructure

Your module can import from the protocol core and infrastructure layers:

```python
# Identity & Crypto
from core.clawnexus_identity import generate_keypair, sign_payload, verify_payload

# Agent SDK
from core.claw_client import ClawClient

# Database
from infrastructure import nexus_db as db

# Economic Engine
from infrastructure.nexus_vault import lock_escrow, release_escrow

# Trust Scores
from core import nexus_trust as trust
```

### 4. Test your module

Run from the repo root:

```bash
python -c "from modules.my_awesome_plugin import MODULE_NAME; print(f'Loaded: {MODULE_NAME}')"
```

Or use the kernel boot report:

```bash
python nexus_kernel.py
```

## Rules

1. **Never modify `core/`** — It's the auditable, open-source protocol. Propose changes via PR.
2. **Never modify `infrastructure/`** — It's the shared plumbing. Extend it, don't edit it.
3. **Keep modules self-contained** — Each module should work independently. If your module crashes, it must not crash the core.
4. **Use `.env` for secrets** — Never hardcode API keys. Use `os.getenv()` with clear variable names.

## Available Core APIs

| Module | Key Functions |
|--------|-------------|
| `core.clawnexus_identity` | `generate_keypair()`, `sign_payload()`, `verify_payload()` |
| `core.claw_client` | `ClawClient.send_mission()`, `ClawClient.poll_loop()` |
| `core.nexus_relay` | `create_app()` — aiohttp relay server |
| `core.nexus_trust` | `calculate_trust_score()`, `get_leaderboard()` |
| `core.claw_pay` | `deposit_funds()`, `withdraw_funds()`, `PaymentProvider` |
| `infrastructure.nexus_db` | `ensure_agent()`, `get_mission()`, `log_transaction()` |
| `infrastructure.nexus_vault` | `lock_escrow()`, `release_escrow()`, `refund_escrow()` |
| `infrastructure.solana_client` | `ClawNexusOnChain` — on-chain escrow SDK |
