# Dockerfile for fast-post-socialv3 on Railway
# All Stripe/API keys are RUNTIME-ONLY env vars — no build-time secrets needed.
# This Dockerfile replaces Railpack auto-detection that incorrectly injects
# STRIPE_PUBLISHABLE_KEY (and similar) as --secret build args.

FROM python:3.12-slim

# Install system dependencies for Playwright/Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first for layer caching
COPY requirements.txt ./

# Install Python dependencies (no build secrets required)
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium and its system deps
ENV PLAYWRIGHT_BROWSERS_PATH=/app/.playwright-browsers
RUN python -m playwright install --with-deps chromium

# Copy the full project
COPY . .

# Runtime environment
ENV PYTHONUNBUFFERED=1
ENV PORT=5000

# Expose port (Railway overrides via PORT env var)
EXPOSE 5000

# Start via start.sh (same as railway.json startCommand)
CMD ["sh", "start.sh"]
