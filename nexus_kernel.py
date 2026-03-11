"""
ClawNexus Kernel — The System Boot Loader.

Discovers and loads all core modules, infrastructure services,
and optional plugins from the /modules directory.

Usage:
    python nexus_kernel.py          # Boot report only
    python nexus_kernel.py --web    # Start the web portal
    python nexus_kernel.py --relay  # Start the relay server
    python nexus_kernel.py --watch  # Start the Discord watchtower
"""

import os
import sys
import importlib
import pkgutil
import logging
import argparse

# Ensure the repo root is on sys.path
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT_DIR, ".env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [NexusKernel] %(levelname)s %(message)s"
)
log = logging.getLogger("NexusKernel")

# ============================================================
# Core Module Registry
# ============================================================
CORE_MODULES = [
    "core.clawnexus_identity",
    "core.claw_client",
    "core.nexus_relay",
    "core.nexus_trust",
    "core.claw_pay",
]

INFRASTRUCTURE_MODULES = [
    "infrastructure.nexus_db",
    "infrastructure.nexus_vault",
    "infrastructure.solana_client",
]


def load_module(module_path: str) -> bool:
    """Attempt to import a module. Returns True on success."""
    try:
        importlib.import_module(module_path)
        return True
    except Exception as e:
        log.warning(f"  ⚠️  Failed to load {module_path}: {e}")
        return False


def discover_plugins() -> list:
    """
    Scan the /modules directory for plugin packages.
    A valid plugin has an __init__.py with MODULE_NAME defined.
    """
    plugins = []
    modules_dir = os.path.join(ROOT_DIR, "modules")

    if not os.path.isdir(modules_dir):
        return plugins

    for item in os.listdir(modules_dir):
        plugin_path = os.path.join(modules_dir, item)
        init_file = os.path.join(plugin_path, "__init__.py")

        if os.path.isdir(plugin_path) and os.path.isfile(init_file):
            try:
                mod = importlib.import_module(f"modules.{item}")
                plugin_info = {
                    "name": getattr(mod, "MODULE_NAME", item),
                    "version": getattr(mod, "MODULE_VERSION", "unknown"),
                    "author": getattr(mod, "MODULE_AUTHOR", "unknown"),
                    "description": getattr(mod, "MODULE_DESCRIPTION", ""),
                    "package": f"modules.{item}",
                }
                plugins.append(plugin_info)
            except Exception as e:
                log.warning(f"  ⚠️  Plugin '{item}' failed to load: {e}")

    return plugins


def boot():
    """
    Boot the ClawNexus system.
    Loads core, infrastructure, discovers plugins, and prints a status report.
    """
    log.info("=" * 60)
    log.info("🦞 ClawNexus Kernel — Booting...")
    log.info("=" * 60)

    # --- Load Core ---
    log.info("\n📦 Loading Core Protocol...")
    core_ok = 0
    for mod in CORE_MODULES:
        if load_module(mod):
            log.info(f"  ✅ {mod}")
            core_ok += 1
    log.info(f"  Core: {core_ok}/{len(CORE_MODULES)} modules loaded.")

    # --- Load Infrastructure ---
    log.info("\n🔧 Loading Infrastructure...")
    infra_ok = 0
    for mod in INFRASTRUCTURE_MODULES:
        if load_module(mod):
            log.info(f"  ✅ {mod}")
            infra_ok += 1
        else:
            log.info(f"  ⏭️  {mod} (skipped — check .env)")
    log.info(f"  Infrastructure: {infra_ok}/{len(INFRASTRUCTURE_MODULES)} modules loaded.")

    # --- Discover Plugins ---
    log.info("\n🔌 Discovering Plugins...")
    plugins = discover_plugins()
    if plugins:
        for p in plugins:
            log.info(f"  ✅ {p['name']} v{p['version']} by {p['author']}")
            if p['description']:
                log.info(f"     └─ {p['description']}")
    else:
        log.info("  No plugins found in /modules.")

    # --- Summary ---
    total = core_ok + infra_ok + len(plugins)
    log.info("\n" + "=" * 60)
    log.info(f"🦞 ClawNexus Kernel — {total} components loaded.")
    log.info(f"   Core: {core_ok} | Infrastructure: {infra_ok} | Plugins: {len(plugins)}")
    log.info("=" * 60)

    return {
        "core": core_ok,
        "infrastructure": infra_ok,
        "plugins": plugins,
        "total": total,
    }


# ============================================================
# CLI Entry Points
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="ClawNexus Kernel — System Boot Loader")
    parser.add_argument("--web", action="store_true", help="Start the web portal")
    parser.add_argument("--relay", action="store_true", help="Start the relay server")
    parser.add_argument("--watch", action="store_true", help="Start the Discord watchtower")
    args = parser.parse_args()

    # Always boot first
    report = boot()

    if args.web:
        log.info("\n🌐 Starting Web Portal...")
        from modules.founder_vibe.nexus_web import app
        import uvicorn
        uvicorn.run(app, host="0.0.0.0", port=8080)

    elif args.relay:
        log.info("\n🛤️  Starting NexusRelay...")
        from core.nexus_relay import create_app
        from aiohttp import web
        relay_app = create_app()
        port = int(os.getenv("RELAY_PORT", 8377))
        web.run_app(relay_app, port=port)

    elif args.watch:
        log.info("\n🗼 Starting Watchtower...")
        from modules.founder_vibe.nexus_watchtower import WatchtowerBot
        bot = WatchtowerBot()
        bot.run(os.getenv("DISCORD_BOT_TOKEN", ""))

    else:
        log.info("\nNo service flag passed. Use --web, --relay, or --watch to start a service.")


if __name__ == "__main__":
    main()
