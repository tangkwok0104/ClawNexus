#!/bin/bash
# ============================================================
# ClawNexus — Package for AWS Deployment
# ============================================================
# Run this on your Mac to bundle ClawNexus and its dependencies.

set -e

echo "📦 Packaging ClawNexus for AWS..."

# Step 1: Generate changelog from git history
echo "📝 Generating changelog from git commits..."
python3 scripts/generate_changelog.py

PKG_DIR="watchtower_deploy"
rm -rf $PKG_DIR
mkdir -p $PKG_DIR

# Step 2: Copy core modules
echo "📁 Copying core modules..."
cp core/claw_client.py $PKG_DIR/
cp core/claw_pay.py $PKG_DIR/
cp core/clawnexus_identity.py $PKG_DIR/
cp core/nexus_relay.py $PKG_DIR/
cp core/nexus_trust.py $PKG_DIR/

# Step 3: Copy infrastructure (flat + package structure for Python imports)
echo "📁 Copying infrastructure..."
cp infrastructure/nexus_db.py $PKG_DIR/
cp infrastructure/nexus_vault.py $PKG_DIR/
cp infrastructure/solana_client.py $PKG_DIR/
# Also preserve the package directory so 'from infrastructure import X' works on the server
mkdir -p $PKG_DIR/infrastructure
cp infrastructure/__init__.py $PKG_DIR/infrastructure/ 2>/dev/null || touch $PKG_DIR/infrastructure/__init__.py
cp infrastructure/nexus_db.py $PKG_DIR/infrastructure/
cp infrastructure/nexus_vault.py $PKG_DIR/infrastructure/
cp infrastructure/solana_client.py $PKG_DIR/infrastructure/

# Step 4: Copy founder_vibe module (web portal)
echo "📁 Copying founder_vibe module..."
cp modules/founder_vibe/nexus_market.py $PKG_DIR/
cp modules/founder_vibe/nexus_registry.py $PKG_DIR/
cp modules/founder_vibe/nexus_watchtower.py $PKG_DIR/
cp modules/founder_vibe/nexus_web.py $PKG_DIR/
cp modules/founder_vibe/translations.py $PKG_DIR/
cp modules/founder_vibe/gorilla_bot.py $PKG_DIR/
cp modules/founder_vibe/changelog.json $PKG_DIR/

# Step 5: Copy static assets
if [ -d "modules/founder_vibe/static" ]; then
    echo "📁 Copying static assets..."
    cp -r modules/founder_vibe/static $PKG_DIR/
fi

# Step 5b: Copy smart contract source for audit page
CONTRACT_SRC="contracts/clawnexus_escrow/programs/clawnexus_escrow/src/lib.rs"
if [ -f "$CONTRACT_SRC" ]; then
    echo "📁 Copying contract source..."
    mkdir -p "$PKG_DIR/contracts/clawnexus_escrow/programs/clawnexus_escrow/src"
    cp "$CONTRACT_SRC" "$PKG_DIR/contracts/clawnexus_escrow/programs/clawnexus_escrow/src/"
fi

# Step 6: Copy requirements and env
cp requirements.txt $PKG_DIR/
if [ -f ".env" ]; then
    cp .env $PKG_DIR/
else
    echo "⚠️  Warning: .env not found! You will need to create it on AWS."
fi

# Step 7: Create the AWS setup script
cat > $PKG_DIR/setup_aws.sh << 'EOF'
#!/bin/bash
# Run this on your AWS server

set -e
echo "🦞 Setting up ClawNexus on AWS..."

DEPLOY_DIR="$HOME/clawnexus"
mkdir -p "$DEPLOY_DIR"

# Move files to deployment directory
cp *.py "$DEPLOY_DIR/"
cp *.json "$DEPLOY_DIR/" 2>/dev/null || true
cp requirements.txt "$DEPLOY_DIR/"
if [ -d "infrastructure" ]; then
    mkdir -p "$DEPLOY_DIR/infrastructure"
    cp -r infrastructure/* "$DEPLOY_DIR/infrastructure/"
    # Clear stale Python cache
    rm -rf "$DEPLOY_DIR/infrastructure/__pycache__"
fi
if [ -d "contracts" ]; then
    cp -r contracts "$DEPLOY_DIR/"
fi
if [ -d "static" ]; then
    cp -r static "$DEPLOY_DIR/"
fi
if [ -f ".env" ]; then
    cp .env "$DEPLOY_DIR/"
fi

cd "$DEPLOY_DIR"

# Set up virtual environment
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
pip install discord.py supabase fastapi uvicorn python-dotenv slowapi -q

# Create systemd service for Watchtower
sudo bash -c "cat > /etc/systemd/system/nexus-watchtower.service << SVCEOF
[Unit]
Description=ClawNexus Watchtower Bot
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$DEPLOY_DIR
Environment=PATH=$DEPLOY_DIR/venv/bin:/usr/bin
EnvironmentFile=$DEPLOY_DIR/.env
ExecStart=$DEPLOY_DIR/venv/bin/python $DEPLOY_DIR/nexus_watchtower.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SVCEOF"

# Create systemd service for Web Portal
sudo bash -c "cat > /etc/systemd/system/nexus-web.service << WEBEOF
[Unit]
Description=ClawNexus Web Portal
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$DEPLOY_DIR
Environment=PATH=$DEPLOY_DIR/venv/bin:/usr/bin
EnvironmentFile=$DEPLOY_DIR/.env
ExecStart=$DEPLOY_DIR/venv/bin/uvicorn nexus_web:app --host 0.0.0.0 --port 8080
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
WEBEOF"

# Create systemd service for Gorilla Community Bot
sudo bash -c "cat > /etc/systemd/system/nexus-gorilla.service << GOEOF
[Unit]
Description=ClawNexus Gorilla Community Manager
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$DEPLOY_DIR
Environment=PATH=$DEPLOY_DIR/venv/bin:/usr/bin
EnvironmentFile=$DEPLOY_DIR/.env
ExecStart=$DEPLOY_DIR/venv/bin/python $DEPLOY_DIR/gorilla_bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
GOEOF"

# Enable and start services
sudo systemctl daemon-reload
sudo systemctl enable nexus-watchtower nexus-web nexus-gorilla
sudo systemctl restart nexus-watchtower nexus-web nexus-gorilla

echo ""
echo "✅ ClawNexus deployed successfully!"
echo "📜 View logs:"
echo "   sudo journalctl -u nexus-watchtower -f"
echo "   sudo journalctl -u nexus-web -f"
echo "   sudo journalctl -u nexus-gorilla -f"
EOF

chmod +x $PKG_DIR/setup_aws.sh

# Step 8: Create deployment package
tar -czf watchtower_package.tar.gz $PKG_DIR
rm -rf $PKG_DIR

echo ""
echo "✅ Packaged successfully into 'watchtower_package.tar.gz'"
echo ""
echo "🚀 DEPLOY:"
echo "   scp -i ~/.ssh/your-key.pem watchtower_package.tar.gz ubuntu@3.27.113.157:~/"
echo "   ssh -i ~/.ssh/your-key.pem ubuntu@3.27.113.157"
echo "   tar -xzf watchtower_package.tar.gz && cd watchtower_deploy && ./setup_aws.sh"
echo "============================================================"
