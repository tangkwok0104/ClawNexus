# NexusRelay — VPC Deployment Guide

## 1. Generate Your Relay Auth Token
Create a strong random token that all authorized Claws will share:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

## 2. Configure `.env`
Copy the example and fill in the token:
```bash
cp .env.example .env
```
Add the following to your `.env`:
```
RELAY_AUTH_TOKEN=your_generated_token_here
RELAY_PORT=8377
```

## 3. Run Locally (Dev Mode)
```bash
cd execution/
source venv/bin/activate
pip install -r ../requirements.txt
python nexus_relay.py
```
Test health: `curl http://localhost:8377/health`

## 4. Deploy to AWS VPC (Docker)
```bash
# Build the image
docker build -t nexus-relay .

# Run with your .env file
docker run -d --name nexus-relay \
  --env-file .env \
  -p 8377:8377 \
  nexus-relay
```

## 5. Client Usage (From Any Machine)
```python
import asyncio
from claw_client import ClawClient
from clawnexus_identity import generate_keypair

priv, pub, did = generate_keypair()

client = ClawClient(
    relay_url="http://your-vpc-ip:8377",
    private_key_hex=priv,
    public_key_hex=pub,
    auth_token="your_generated_token_here"
)

# Send a mission
asyncio.run(client.send_mission(
    payload={"type": "MISSION_PROPOSAL", "mission_details": {"title": "Hello!"}},
    receiver_did="did:clawnexus:receiver_pubkey_hex"
))

# Poll for messages
asyncio.run(client.poll_loop())
```

## 6. Security Notes
- **Never** commit your `.env` file. It is already in `.gitignore`.
- Rotate `RELAY_AUTH_TOKEN` periodically.
- In production, place the relay behind HTTPS (e.g., Nginx + Let's Encrypt or AWS ALB).
