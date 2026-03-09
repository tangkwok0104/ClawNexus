# ClawNexus Discord Bot Setup Guide

## 1. Create the Bot Application
1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click **New Application** → name it **ClawNexus Watchtower**
3. Go to **Bot** (left sidebar) → Click **Reset Token** → Copy the token

## 2. Enable Required Intents
On the **Bot** page, scroll down and enable:
- ✅ **Message Content Intent**

## 3. Invite the Bot to Your Server
1. Go to **OAuth2 → URL Generator**
2. Check **bot** under Scopes
3. Check **Administrator** under Bot Permissions
4. Copy the generated URL and open it in your browser to invite the bot

## 4. Find Your Channel ID and User ID
1. In Discord Settings → **Advanced** → Enable **Developer Mode**
2. Right-click the channel you want notifications in → **Copy Channel ID**
3. Right-click your own username → **Copy User ID**

## 5. Configure `.env`
Add these to your `.env` file (in the project root):
```
DISCORD_BOT_TOKEN=your_bot_token_here
DISCORD_CHANNEL_ID=your_channel_id_here
DISCORD_OWNER_ID=your_user_id_here
RELAY_URL=http://3.27.113.157:8377
RELAY_AUTH_TOKEN=your_relay_token_here
```

## 6. Run the Watchtower
```bash
cd execution/
source venv/bin/activate
pip install discord.py aiohttp python-dotenv cryptography
python nexus_watchtower.py
```

## 7. Send a Test Mission
In a second terminal:
```bash
cd execution/
source venv/bin/activate
python send_test_mission.py
```

You should see a rich embed appear in your Discord channel with **Approve ✅** and **Reject ❌** buttons.
