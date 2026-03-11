#!/usr/bin/env python3
"""
Auto-generate changelog.json from git commit history.
Run before deploy to keep the project log up-to-date.

Usage: python scripts/generate_changelog.py
"""

import subprocess
import json
import re
import os
from datetime import datetime

# Output path
CHANGELOG_PATH = os.path.join(
    os.path.dirname(__file__),
    "..", "modules", "founder_vibe", "changelog.json"
)

# Map conventional commit prefixes to changelog entry types
TYPE_MAP = {
    "feat": ("feature", "sparkles"),
    "feature": ("feature", "sparkles"),
    "fix": ("fix", "gear"),
    "docs": ("update", "book"),
    "style": ("update", "sparkles"),
    "refactor": ("update", "gear"),
    "perf": ("update", "bolt"),
    "test": ("update", "shield"),
    "chore": ("update", "package"),
    "build": ("update", "package"),
    "ci": ("update", "gear"),
}

# Special keywords that indicate announcements
ANNOUNCEMENT_KEYWORDS = ["launch", "release", "v1", "v2", "mainnet", "phase"]

# Icon mapping for variety
ICON_ROTATION = ["rocket", "sparkles", "bolt", "shield", "globe", "fire", "star"]


def parse_commit_type(message: str) -> tuple:
    """Parse commit message and return (type, icon, clean_title)."""
    message = message.strip()

    # Check for conventional commit format: type(scope): message or type: message
    match = re.match(r'^(\w+)(?:\([^)]+\))?[:\s]+(.+)$', message, re.IGNORECASE)

    if match:
        prefix = match.group(1).lower()
        title = match.group(2).strip()

        if prefix in TYPE_MAP:
            entry_type, icon = TYPE_MAP[prefix]
            # Capitalize first letter of title
            title = title[0].upper() + title[1:] if title else title
            return entry_type, icon, title

    # Check for announcement keywords
    lower_msg = message.lower()
    for keyword in ANNOUNCEMENT_KEYWORDS:
        if keyword in lower_msg:
            return "announcement", "rocket", message

    # Default to update
    return "update", "star", message


def get_git_log(max_entries: int = 20) -> list:
    """Get git log entries."""
    try:
        result = subprocess.run(
            ["git", "log", f"-{max_entries}", "--format=%H|%ad|%s", "--date=short"],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(__file__))
        )

        if result.returncode != 0:
            print(f"Git error: {result.stderr}")
            return []

        entries = []
        seen_titles = set()  # Dedupe similar entries

        for line in result.stdout.strip().split('\n'):
            if not line:
                continue

            parts = line.split('|', 2)
            if len(parts) != 3:
                continue

            commit_hash, date, message = parts
            entry_type, icon, title = parse_commit_type(message)

            # Skip if we've seen a very similar title
            title_key = title.lower()[:30]
            if title_key in seen_titles:
                continue
            seen_titles.add(title_key)

            # Generate version based on type and position
            entries.append({
                "date": date,
                "hash": commit_hash[:7],
                "type": entry_type,
                "icon": icon,
                "title": title,
                "message": message
            })

        return entries

    except FileNotFoundError:
        print("Git not found")
        return []


def generate_versions(entries: list) -> list:
    """Assign version numbers to entries."""
    # Start from 1.0.0, increment based on type
    major, minor, patch = 1, 0, 0

    # Process in reverse (oldest first) to build up versions
    versioned = []
    for entry in reversed(entries):
        if entry["type"] == "announcement":
            minor += 1
            patch = 0
        elif entry["type"] == "feature":
            patch += 1
        else:
            patch += 1

        versioned.append({
            "date": entry["date"],
            "version": f"{major}.{minor}.{patch}",
            "type": entry["type"],
            "icon": entry["icon"],
            "title": entry["title"],
            "description": entry["message"]
        })

    # Return in reverse chronological order (newest first)
    return list(reversed(versioned))


def main():
    print("Generating changelog from git history...")

    entries = get_git_log(20)
    if not entries:
        print("No git entries found")
        return

    versioned = generate_versions(entries)

    changelog = {"entries": versioned}

    # Ensure directory exists
    os.makedirs(os.path.dirname(CHANGELOG_PATH), exist_ok=True)

    with open(CHANGELOG_PATH, 'w', encoding='utf-8') as f:
        json.dump(changelog, f, indent=2, ensure_ascii=False)

    print(f"Generated {len(versioned)} changelog entries")
    print(f"Saved to: {CHANGELOG_PATH}")

    # Show latest 3
    print("\nLatest entries:")
    for entry in versioned[:3]:
        print(f"  v{entry['version']} - {entry['title']}")


if __name__ == "__main__":
    main()
