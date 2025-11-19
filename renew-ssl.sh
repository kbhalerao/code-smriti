#!/bin/bash
# SSL Certificate Renewal Script
# Run this via cron: 0 3 * * * /path/to/renew-ssl.sh

set -e

DOMAIN="${1}"
CF_API_TOKEN="${CLOUDFLARE_API_TOKEN}"

if [ -z "$DOMAIN" ]; then
    echo "Usage: $0 <domain>"
    echo "Example: $0 codesmriti.example.com"
    exit 1
fi

if [ -z "$CF_API_TOKEN" ]; then
    echo "Error: CLOUDFLARE_API_TOKEN not set"
    exit 1
fi

echo "Renewing SSL certificate for $DOMAIN..."

# Ensure Cloudflare credentials file exists
if [ ! -f "api-gateway/certbot/cloudflare.ini" ]; then
    cat > api-gateway/certbot/cloudflare.ini <<EOF
dns_cloudflare_api_token = ${CF_API_TOKEN}
EOF
    chmod 600 api-gateway/certbot/cloudflare.ini
fi

# Run Certbot renewal
docker run --rm \
  -v "$(pwd)/api-gateway/ssl:/etc/letsencrypt" \
  -v "$(pwd)/api-gateway/certbot:/root/.secrets" \
  certbot/dns-cloudflare renew \
  --dns-cloudflare \
  --dns-cloudflare-credentials /root/.secrets/cloudflare.ini \
  --quiet

# Reload nginx if renewal succeeded
if [ $? -eq 0 ]; then
    echo "Certificate renewed successfully. Reloading nginx..."
    docker-compose exec api-gateway nginx -s reload
    echo "✓ SSL certificate renewed and nginx reloaded"
else
    echo "✗ Certificate renewal failed"
    exit 1
fi
