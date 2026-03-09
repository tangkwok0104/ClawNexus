# ── ClawNexus NexusRelay — Production Dockerfile ──
FROM python:3.13-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy execution scripts (relay + identity module)
COPY execution/nexus_relay.py .
COPY execution/clawnexus_identity.py .

# Expose the relay port
EXPOSE 8377

# Run the relay server
CMD ["python", "nexus_relay.py"]
