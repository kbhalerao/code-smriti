# Docker Options for CodeSmriti

CodeSmriti supports two Docker runtime options. Choose based on your access method.

## Quick Comparison

| Feature | Docker Desktop | Colima |
|---------|---------------|--------|
| **GUI Required** | Yes | No |
| **SSH Compatible** | No* | Yes |
| **Resource Usage** | Higher | Lower |
| **Ease of Use** | Easier (GUI) | CLI only |
| **Installation** | `./quick-install.sh` | `./quick-install-headless.sh` |
| **Management** | GUI + CLI | CLI only |
| **Performance** | Excellent | Excellent |
| **Best For** | Local development | Remote/SSH access |

*Docker Desktop can work with SSH but requires GUI for initial setup

## When to Use Docker Desktop

✅ **Use Docker Desktop if:**
- You have physical access to the Mac
- You prefer GUI management tools
- You're new to Docker
- You want the official Docker experience

**Install with:**
```bash
./quick-install.sh
```

## When to Use Colima

✅ **Use Colima if:**
- You're accessing via SSH only
- You don't want/need GUI
- You want lighter resource usage
- You prefer command-line tools
- Docker Desktop licensing is a concern

**Install with:**
```bash
./quick-install-headless.sh
```

## Detailed Comparison

### Docker Desktop

**Pros:**
- Official Docker application
- Nice GUI for container management
- Integrated Kubernetes support
- Easy updates via GUI
- Visual logs viewer
- Volume browser

**Cons:**
- Requires GUI access
- Higher memory overhead (~1-2GB)
- Larger disk footprint
- Commercial license required for large enterprises

**Management:**
```bash
# Start/stop via GUI or CLI
docker-compose up -d
docker-compose down

# Check status
open -a Docker    # Open Docker Desktop app
```

### Colima

**Pros:**
- Fully open source
- No GUI required
- Lower resource overhead
- Perfect for SSH/remote
- Lightweight
- Fast startup

**Cons:**
- CLI only (no GUI)
- Less beginner-friendly
- Need to remember CLI commands

**Management:**
```bash
# Start Colima
colima start

# Stop Colima
colima stop

# Check status
colima status

# Manage CodeSmriti services
docker-compose up -d
docker-compose down
```

## Switching Between Options

### From Docker Desktop to Colima

```bash
# 1. Stop Docker Desktop
docker-compose down

# 2. Quit Docker Desktop application

# 3. Install Colima
brew install colima

# 4. Start Colima
colima start --cpu 4 --memory 8 --disk 100 --arch aarch64

# 5. Restart CodeSmriti
docker-compose up -d
```

### From Colima to Docker Desktop

```bash
# 1. Stop Colima
docker-compose down
colima stop

# 2. Install Docker Desktop
brew install --cask docker

# 3. Start Docker Desktop (GUI)
open -a Docker

# 4. Wait for Docker to be ready, then start CodeSmriti
docker-compose up -d
```

## Resource Configuration

### Docker Desktop

Configure via GUI:
1. Docker Desktop → Preferences → Resources
2. Set CPUs, Memory, Disk
3. Recommended for CodeSmriti:
   - CPUs: 4
   - Memory: 8GB
   - Disk: 100GB

### Colima

Configure via CLI:
```bash
# Stop Colima first
colima stop

# Start with custom resources
colima start \
  --cpu 4 \
  --memory 8 \
  --disk 100 \
  --arch aarch64 \
  --vm-type vz \
  --mount-type virtiofs

# Or edit config file
nano ~/.colima/default/colima.yaml
```

## Troubleshooting

### Docker Desktop Issues

**Won't start:**
```bash
# Reset Docker Desktop
rm -rf ~/Library/Group\ Containers/group.com.docker
brew reinstall --cask docker
```

**Port conflicts:**
- Check Docker Desktop → Preferences → Resources → Network

### Colima Issues

**Won't start:**
```bash
# Reset Colima
colima delete
colima start --cpu 4 --memory 8 --disk 100 --arch aarch64
```

**Port conflicts:**
```bash
# Check what's using ports
lsof -i :80
lsof -i :8080

# Stop conflicting services or use different ports in docker-compose.yml
```

## Performance Tips

Both options perform well, but here are some tips:

### For Docker Desktop:
- Allocate enough memory (8GB minimum)
- Use VirtioFS for file sharing (default on modern versions)
- Disable features you don't need (Kubernetes, etc.)

### For Colima:
- Use VZ virtualization for M3 (default in install script)
- Use VirtioFS for better file system performance
- Allocate resources based on available RAM

## SSH Tunneling for Remote Access

If using Colima on a remote Mac, tunnel ports to your local machine:

```bash
# From your local machine
ssh -L 8080:localhost:8080 \
    -L 8091:localhost:8091 \
    -L 80:localhost:80 \
    user@remote-mac
```

Then access services locally:
- MCP Server: http://localhost:8080
- Couchbase UI: http://localhost:8091
- API Gateway: http://localhost

## Recommendations

### For Most Users
Start with **Docker Desktop** if you have GUI access. It's easier to learn and manage.

### For Production/Remote Servers
Use **Colima** for headless deployments and SSH-only access.

### For Development Teams
**Docker Desktop** for local development, **Colima** for shared development servers.

---

**Both options work perfectly with CodeSmriti.** Choose based on your access method and preferences.
