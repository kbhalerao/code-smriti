# SSL Setup Guide for CodeSmriti

This guide explains how to set up SSL/TLS certificates for your CodeSmriti deployment using Let's Encrypt and Cloudflare.

## Prerequisites

1. **Domain name** pointing to your server
2. **Cloudflare account** with your domain managed by Cloudflare
3. **Cloudflare API Token** with DNS edit permissions

## Getting a Cloudflare API Token

1. Go to https://dash.cloudflare.com/profile/api-tokens
2. Click "Create Token"
3. Use the "Edit zone DNS" template
4. Select your zone (domain)
5. Create the token and copy it

## Step 1: Stop Foreground Containers (if running)

If you have containers running in the foreground, press `Ctrl+C`, then:

```bash
docker-compose up -d
```

## Step 2: Set Up SSL Certificates

Add your Cloudflare API token to your `.env` file:

```bash
echo "CLOUDFLARE_API_TOKEN=your_token_here" >> .env
```

Run the SSL setup script:

```bash
./setup-ssl.sh your-domain.com your-email@example.com
```

This will:
- Create the necessary directories
- Run Certbot with Cloudflare DNS challenge
- Obtain SSL certificates from Let's Encrypt

## Step 3: Update Nginx Configuration

1. Edit `api-gateway/nginx-ssl.conf` and replace `DOMAIN` with your actual domain:

```bash
sed -i 's/DOMAIN/your-domain.com/g' api-gateway/nginx-ssl.conf
```

2. Replace `server_name _;` with your actual domain:

```bash
sed -i 's/server_name _;/server_name your-domain.com;/g' api-gateway/nginx-ssl.conf
```

3. Backup current config and switch to SSL config:

```bash
mv api-gateway/nginx.conf api-gateway/nginx-http.conf
cp api-gateway/nginx-ssl.conf api-gateway/nginx.conf
```

## Step 4: Restart Nginx

```bash
docker-compose restart api-gateway
```

## Step 5: Verify SSL

Test your SSL setup:

```bash
curl -I https://your-domain.com/health
```

You should see `HTTP/2 200` and security headers.

## Step 6: Set Up Auto-Renewal

Add a cron job to renew certificates automatically (every day at 3 AM):

```bash
crontab -e
```

Add this line:

```cron
0 3 * * * cd /path/to/code-smriti && ./renew-ssl.sh your-domain.com >> /var/log/ssl-renew.log 2>&1
```

## Cloudflare Settings

If you're using Cloudflare as a proxy (orange cloud):

1. **SSL/TLS Mode**: Set to "Full (strict)" in Cloudflare dashboard
2. **Always Use HTTPS**: Enable
3. **Minimum TLS Version**: TLS 1.2 or higher
4. **Automatic HTTPS Rewrites**: Enable

## Auto-Start on Reboot

All services are configured with `restart: unless-stopped` and will automatically start when Docker starts.

To ensure Docker starts on boot:

**On Linux (systemd):**
```bash
sudo systemctl enable docker
```

**On macOS:**
Docker Desktop starts automatically by default.

## Firewall Configuration

Make sure ports 80 and 443 are open:

```bash
# UFW (Ubuntu/Debian)
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# firewalld (RHEL/CentOS)
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
```

## Troubleshooting

### Certificate not found

If nginx can't find certificates, check:

```bash
ls -la api-gateway/ssl/live/your-domain.com/
```

### Cloudflare API errors

Verify your API token has the correct permissions:

```bash
curl -X GET "https://api.cloudflare.com/client/v4/user/tokens/verify" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN"
```

### Nginx configuration test

Test nginx config before restarting:

```bash
docker-compose exec api-gateway nginx -t
```

## NAT/Port Forwarding

If your server is behind NAT, forward these ports to your server's internal IP:

- Port 80 (HTTP) → Server:80
- Port 443 (HTTPS) → Server:443

## Security Notes

1. **Never commit** `.env` file or `api-gateway/certbot/cloudflare.ini` to git
2. **Rotate API tokens** periodically
3. **Monitor certificate expiry** - Let's Encrypt certificates expire in 90 days
4. **Enable Cloudflare WAF** for additional security
5. **Consider restricting Couchbase UI** access in production

## Production Checklist

- [ ] SSL certificates obtained and configured
- [ ] Nginx using SSL configuration
- [ ] HTTP redirects to HTTPS working
- [ ] Auto-renewal cron job configured
- [ ] Cloudflare proxy enabled (orange cloud)
- [ ] Cloudflare SSL mode: Full (strict)
- [ ] Firewall ports open (80, 443)
- [ ] NAT/port forwarding configured
- [ ] Docker set to start on boot
- [ ] All services have `restart: unless-stopped`
- [ ] Couchbase admin password changed
- [ ] JWT_SECRET changed from default
- [ ] Test SSL: https://www.ssllabs.com/ssltest/
