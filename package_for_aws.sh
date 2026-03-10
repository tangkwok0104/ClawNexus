#!/bin/bash
# ============================================================
# ClawNexus — Package Watchtower for AWS Deployment
# ============================================================
# Run this on your Mac to bundle the Watchtower and its dependencies.

echo "📦 Packaging ClawNexus Watchtower for AWS..."

PKG_DIR="watchtower_deploy"
mkdir -p $PKG_DIR

# Copy execution scripts
cp execution/nexus_watchtower.py $PKG_DIR/
cp execution/nexus_vault.py $PKG_DIR/
cp execution/nexus_db.py $PKG_DIR/
cp execution/claw_pay.py $PKG_DIR/
cp execution/clawnexus_identity.py $PKG_DIR/
cp execution/nexus_trust.py $PKG_DIR/
cp execution/nexus_registry.py $PKG_DIR/
cp execution/nexus_market.py $PKG_DIR/
cp execution/nexus_web.py $PKG_DIR/
cp execution/solana_client.py $PKG_DIR/
cp execution/translations.py $PKG_DIR/
cp requirements.txt $PKG_DIR/

# Copy static assets (hero video, images)
if [ -d "execution/static" ]; then
    cp -r execution/static $PKG_DIR/
fi

# Copy environment variables (CRITICAL: this includes Discord and Supabase keys)
if [ -f ".env" ]; then
    cp .env $PKG_DIR/
else
    echo "⚠️ Warning: .env not found! You will need to create it on AWS."
fi

# Create the AWS setup script
cat > $PKG_DIR/setup_watchtower_aws.sh << 'EOF'
#!/bin/bash
# Run this on your AWS server

set -e
echo "🦞 Setting up ClawNexus Watchtower on AWS..."

DEPLOY_DIR="$HOME/clawnexus"
mkdir -p "$DEPLOY_DIR"

# Move files to deployment directory
cp *.py "$DEPLOY_DIR/"
cp requirements.txt "$DEPLOY_DIR/"
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
pip install discord.py supabase fastapi uvicorn -q

# Create systemd service
sudo bash -c "cat > /etc/systemd/system/nexus-watchtower.service << SVCEOF
[Unit]
Description=ClawNexus Watchtower Bot
After=network.target nexus-relay.service

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

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable nexus-watchtower
sudo systemctl restart nexus-watchtower

echo "✅ Watchtower deployed and running as a systemd service!"
echo "📜 View logs with: sudo journalctl -u nexus-watchtower -f"

# Create Web Portal systemd service
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
ExecStart=$DEPLOY_DIR/venv/bin/python $DEPLOY_DIR/nexus_web.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
WEBEOF"

sudo systemctl daemon-reload
sudo systemctl enable nexus-web
sudo systemctl restart nexus-web

echo "✅ Web Portal deployed on port 8080!"
echo "📜 View logs with: sudo journalctl -u nexus-web -f"
EOF

chmod +x $PKG_DIR/setup_watchtower_aws.sh

# Zip it up
tar -czf watchtower_package.tar.gz $PKG_DIR
rm -rf $PKG_DIR

echo "✅ Packaged successfully into 'watchtower_package.tar.gz'"
echo ""
echo "🚀 NEXT STEPS:"
echo "1. Upload to your AWS server:"
echo "   scp -i /path/to/your/key.pem watchtower_package.tar.gz ubuntu@3.27.113.157:~/"
echo "2. SSH into your AWS server:"
echo "   ssh -i /path/to/your/key.pem ubuntu@3.27.113.157"
echo "3. Extract and install:"
echo "   tar -xzf watchtower_package.tar.gz"
echo "   cd watchtower_deploy"
echo "   ./setup_watchtower_aws.sh"
echo "============================================================"
