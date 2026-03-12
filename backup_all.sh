#!/bin/bash

# Configuration
COLOR_GREEN='\033[0;32m'
COLOR_BLUE='\033[0;34m'
COLOR_YELLOW='\033[1;33m'
COLOR_RESET='\033[0m'
TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")
COMMIT_MSG="Auto-sync backup at $TIMESTAMP"

echo -e "${COLOR_BLUE}=======================================${COLOR_RESET}"
echo -e "${COLOR_BLUE}🚀 Starting ClawNexus One-Click Backup${COLOR_RESET}"
echo -e "${COLOR_BLUE}=======================================${COLOR_RESET}\n"

# 1. Sync Main Project Repository (ClawNexus)
echo -e "${COLOR_YELLOW}[1/2] Syncing Main Project (ClawNexus)...${COLOR_RESET}"
git add .
git commit -m "$COMMIT_MSG"
if git push; then
    echo -e "${COLOR_GREEN}✓ Main project pushed successfully.${COLOR_RESET}\n"
else
    echo -e "\n${COLOR_YELLOW}⚠️ Note: Main project might be up to date or push failed. Check output above.${COLOR_RESET}\n"
fi

# 2. Sync Secret Sauce Repository
echo -e "${COLOR_YELLOW}[2/2] Syncing Private Blueprints (_SECRET_SAUCE)...${COLOR_RESET}"
cd _SECRET_SAUCE || { echo "Error: _SECRET_SAUCE directory not found!"; exit 1; }
git add .
git commit -m "$COMMIT_MSG"
if git push; then
    echo -e "${COLOR_GREEN}✓ Secret Sauce pushed successfully.${COLOR_RESET}\n"
else
    echo -e "\n${COLOR_YELLOW}⚠️ Note: Secret Sauce might be up to date or push failed. Check output above.${COLOR_RESET}\n"
fi

# Done!
cd ..
echo -e "${COLOR_BLUE}=======================================${COLOR_RESET}"
echo -e "${COLOR_GREEN}✅ All backups completed at $TIMESTAMP!${COLOR_RESET}"
echo -e "${COLOR_BLUE}=======================================${COLOR_RESET}"
