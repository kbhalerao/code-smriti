#!/bin/bash
# SSL Setup with Certbot and Cloudflare DNS Challenge
# Usage: ./setup-ssl.sh yourdomain.com your-email@example.com

set -e

DOMAIN="${1}"
EMAIL="${2}"
CF_API_TOKEN="${CLOUDFLARE_API_TOKEN}"

if [ -z "$DOMAIN" ] || [ -z "$EMAIL" ]; then
    echo "Usage: $0 <domain> <email>"
    echo "Example: $0 codesmriti.example.com admin@example.com"
    echo ""
    echo "Make sure CLOUDFLARE_API_TOKEN is set in your environment or .env file"
    exit 1
fi

if [ -z "$CF_API_TOKEN" ]; then
    echo "Error: CLOUDFLARE_API_TOKEN not set"
    echo "Get an API token from: https://dash.cloudflare.com/profile/api-tokens"
    echo "Required permissions: Zone:DNS:Edit"
    exit 1
fi

echo "Setting up SSL certificates for $DOMAIN..."

# Create SSL directory
mkdir -p api-gateway/ssl
mkdir -p api-gateway/certbot

# Create Cloudflare credentials file
cat > api-gateway/certbot/cloudflare.ini <<EOF
dns_cloudflare_api_token = ${CF_API_TOKEN}
EOF
chmod 600 api-gateway/certbot/cloudflare.ini

echo "Running Certbot with Cloudflare DNS challenge..."

# Run Certbot in Docker
docker run -it --rm \
  -v "$(pwd)/api-gateway/ssl:/etc/letsencrypt" \
  -v "$(pwd)/api-gateway/certbot:/root/.secrets" \
  certbot/dns-cloudflare certonly \
  --dns-cloudflare \
  --dns-cloudflare-credentials /root/.secrets/cloudflare.ini \
  --email "$EMAIL" \
  --agree-tos \
  --non-interactive \
  --domain "$DOMAIN"

# Copy certificates to nginx SSL directory
if [ -d "api-gateway/ssl/live/$DOMAIN" ]; then
    echo "✓ Certificates obtained successfully!"
    echo "Certificates located at: api-gateway/ssl/live/$DOMAIN/"
    echo ""
    echo "Next steps:"
    echo "1. Update api-gateway/nginx.conf to use the certificates"
    echo "2. Restart nginx: docker-compose restart api-gateway"
else
    echo "✗ Certificate generation failed"
    exit 1
fi
