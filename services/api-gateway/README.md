# CodeSmriti API Gateway

Nginx reverse proxy configuration for the CodeSmriti MCP server and Couchbase. Provides load balancing, SSL termination, and security headers for production deployments.

## Quick Start

### Development (HTTP)

```bash
cd /home/user/code-smriti/4-consume/api-gateway

# Start with HTTP configuration
docker-compose up -d nginx

# Or use standalone nginx
nginx -c $(pwd)/nginx.conf
```

Access the services:
- **MCP Server**: http://localhost/mcp/
- **API**: http://localhost/api/
- **Health Check**: http://localhost/health
- **Couchbase UI**: http://localhost/couchbase/ (development only)

### Production (HTTPS)

```bash
# Copy SSL configuration
cp nginx-ssl.conf nginx.conf

# Update domain name in nginx.conf
sed -i 's/DOMAIN/your-domain.com/g' nginx.conf

# Generate SSL certificates (Let's Encrypt)
certbot certonly --webroot -w /var/www/certbot -d your-domain.com

# Start nginx with SSL
docker-compose up -d nginx
```

## Configuration Files

### nginx.conf (HTTP)
Basic HTTP reverse proxy for development and testing.

**Features:**
- Proxies MCP and API endpoints to mcp-server:8080
- Gzip compression for text responses
- WebSocket support for MCP protocol
- 600s timeouts for long-running queries
- Optional Couchbase UI proxying

### nginx-ssl.conf (HTTPS)
Production-ready HTTPS configuration with security hardening.

**Features:**
- HTTP to HTTPS redirect (except health check and ACME challenges)
- TLS 1.2 and 1.3 support
- Mozilla Intermediate SSL configuration
- HSTS, X-Frame-Options, and other security headers
- Cloudflare real IP detection
- Let's Encrypt ACME challenge support

## SSL Setup

### Option 1: Let's Encrypt (Recommended)

```bash
# Install certbot
apt-get install certbot

# Get certificates (HTTP-01 challenge)
certbot certonly --webroot \
  -w /var/www/certbot \
  -d your-domain.com \
  -d www.your-domain.com

# Certificates will be at:
# /etc/letsencrypt/live/your-domain.com/fullchain.pem
# /etc/letsencrypt/live/your-domain.com/privkey.pem

# Auto-renewal (add to crontab)
0 0 * * * certbot renew --quiet
```

### Option 2: Cloudflare Proxy

If using Cloudflare:

1. Set DNS to "Proxied" (orange cloud)
2. Cloudflare handles SSL termination
3. Configure nginx with Cloudflare origin certificates
4. Real IP detection is already configured in nginx-ssl.conf

### Option 3: Self-Signed (Development Only)

```bash
# Generate self-signed certificate
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout /etc/nginx/ssl/key.pem \
  -out /etc/nginx/ssl/cert.pem

# Update nginx-ssl.conf paths
ssl_certificate /etc/nginx/ssl/cert.pem;
ssl_certificate_key /etc/nginx/ssl/key.pem;
```

## Deployment

### Docker Compose (Recommended)

```yaml
version: '3.8'

services:
  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - /etc/letsencrypt:/etc/letsencrypt:ro
      - /var/www/certbot:/var/www/certbot:ro
    depends_on:
      - mcp-server
    restart: unless-stopped

  mcp-server:
    build: ../mcp-server
    expose:
      - "8080"
    restart: unless-stopped
```

```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f nginx

# Reload configuration
docker-compose exec nginx nginx -s reload
```

### Standalone Deployment

```bash
# Install nginx
apt-get install nginx

# Copy configuration
cp nginx-ssl.conf /etc/nginx/nginx.conf

# Test configuration
nginx -t

# Start nginx
systemctl start nginx
systemctl enable nginx

# Reload after changes
systemctl reload nginx
```

## Endpoint Routing

| Path | Upstream | Description |
|------|----------|-------------|
| `/health` | mcp-server:8080 | Health check (allowed in plain HTTP) |
| `/mcp/*` | mcp-server:8080 | MCP protocol endpoints |
| `/api/*` | mcp-server:8080 | REST API endpoints |
| `/` | mcp-server:8080 | Root endpoint |
| `/couchbase/*` | couchbase:8091 | Couchbase UI (dev only) |

## Security Features

### HTTP Security Headers

```nginx
Strict-Transport-Security: max-age=31536000; includeSubDomains
X-Frame-Options: SAMEORIGIN
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block
```

### Cloudflare Integration

Real IP detection configured for all Cloudflare IP ranges. The `CF-Connecting-IP` header is used to get the actual client IP.

### Rate Limiting (Optional)

Uncomment in nginx.conf to enable:

```nginx
# In http block
limit_req_zone $binary_remote_addr zone=api_limit:10m rate=10r/s;

# In location /api/
limit_req zone=api_limit burst=20 nodelay;
```

## Performance Tuning

### Connection Settings

```nginx
worker_connections 1024;      # Max connections per worker
keepalive_timeout 65;         # Keep connections alive
tcp_nopush on;               # Send headers in one packet
tcp_nodelay on;              # Don't buffer data
```

### Compression

Gzip enabled for:
- text/plain
- text/css
- text/xml
- text/javascript
- application/json
- application/javascript
- application/xml+rss

### Timeouts for MCP

```nginx
proxy_connect_timeout 600s;  # Connection to upstream
proxy_send_timeout 600s;     # Sending request
proxy_read_timeout 600s;     # Reading response
```

Long timeouts needed for:
- Vector search on large codebases
- Embedding generation
- Complex MCP tool calls

## Monitoring

### Access Logs

```bash
# View access logs
tail -f /var/log/nginx/access.log

# Filter by endpoint
grep "/mcp/" /var/log/nginx/access.log

# Count requests by endpoint
awk '{print $7}' /var/log/nginx/access.log | sort | uniq -c
```

### Error Logs

```bash
# View error logs
tail -f /var/log/nginx/error.log

# Filter by severity
grep "\[error\]" /var/log/nginx/error.log
```

### Health Checks

```bash
# Check nginx is running
curl http://localhost/health

# Check SSL configuration
curl https://your-domain.com/health

# Test SSL certificate
openssl s_client -connect your-domain.com:443 -servername your-domain.com
```

## Troubleshooting

### nginx won't start

```bash
# Test configuration
nginx -t

# Check for port conflicts
lsof -ti:80 | xargs kill -9
lsof -ti:443 | xargs kill -9

# Check logs
journalctl -u nginx -f
```

### SSL certificate errors

```bash
# Verify certificates exist
ls -la /etc/letsencrypt/live/DOMAIN/

# Test certificate validity
openssl x509 -in /etc/letsencrypt/live/DOMAIN/fullchain.pem -text -noout

# Renew certificates
certbot renew --force-renewal
```

### 502 Bad Gateway

```bash
# Check upstream (mcp-server) is running
curl http://localhost:8080/health

# Check Docker network
docker network inspect codesmriti_default

# Verify upstream configuration in nginx.conf
nginx -T | grep upstream
```

### WebSocket connection issues

```bash
# Verify upgrade headers are set
curl -i -N -H "Connection: Upgrade" -H "Upgrade: websocket" http://localhost/mcp/

# Check proxy timeout settings
nginx -T | grep proxy_read_timeout
```

## Production Checklist

- [ ] Replace `server_name _;` with actual domain
- [ ] Generate SSL certificates (Let's Encrypt or Cloudflare)
- [ ] Update SSL certificate paths in nginx-ssl.conf
- [ ] Enable rate limiting for /api/ endpoints
- [ ] Remove or restrict /couchbase/ endpoint
- [ ] Configure firewall (allow 80, 443; block 8080, 8091)
- [ ] Set up log rotation for nginx logs
- [ ] Configure monitoring/alerting
- [ ] Test SSL configuration with SSL Labs
- [ ] Set up automatic certificate renewal

## Advanced Configuration

### Custom Domains for Different Services

```nginx
server {
    listen 443 ssl http2;
    server_name mcp.yourdomain.com;

    location / {
        proxy_pass http://mcp_server/mcp/;
        # ... proxy settings
    }
}

server {
    listen 443 ssl http2;
    server_name api.yourdomain.com;

    location / {
        proxy_pass http://mcp_server/api/;
        # ... proxy settings
    }
}
```

### IP Whitelisting

```nginx
# Allow specific IPs
geo $allowed_ip {
    default 0;
    192.168.1.0/24 1;
    10.0.0.0/8 1;
}

server {
    location /api/ {
        if ($allowed_ip = 0) {
            return 403;
        }
        proxy_pass http://mcp_server/api/;
    }
}
```

### Load Balancing (Multiple MCP Servers)

```nginx
upstream mcp_server {
    least_conn;  # Load balancing method
    server mcp-server-1:8080;
    server mcp-server-2:8080;
    server mcp-server-3:8080;
}
```

## Related Documentation

- **MCP Server**: See `../mcp-server/README.md` for backend configuration
- **SSL Setup**: [Let's Encrypt Documentation](https://letsencrypt.org/docs/)
- **Nginx**: [Official Nginx Documentation](https://nginx.org/en/docs/)
- **Cloudflare**: [Cloudflare IP Ranges](https://www.cloudflare.com/ips/)
