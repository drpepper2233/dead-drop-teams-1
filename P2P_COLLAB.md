# P2P Collaboration System

**Host a collaboration room on your laptop. No central server needed.**

## Architecture

```
Host Laptop (Jesse)                    Guest Laptop (Andrew)
┌─────────────────────┐               ┌─────────────────────┐
│  Dead Drop Collab   │               │   Claude Code       │
│  Container :9500    │◄──────────────┤   Agents            │
│                     │   Network     │                     │
│  - juno             │               │   - chief           │
│  - spartan          │               │   - arbiter         │
│  - cortana          │               │   - halsey          │
└─────────────────────┘               └─────────────────────┘
```

**Key Concept:** One person hosts a Docker container, shares the IP, both teams connect.

## Quick Start

### Host (Jesse)

```bash
cd ~/dead-drop-collab
./start.sh
```

Container displays connection info:
- **HOST:** Your connection URL (localhost:9500)
- **GUEST:** Connection URL for Andrew (your-ip:9500)
- **QUICK COPY:** Just the URL to send Andrew

### Guest (Andrew)

1. Receive URL from host: `http://192.168.1.100:9500/mcp`
2. Add to `~/.mcp.json`:
   ```json
   {
     "mcpServers": {
       "dead-drop-collab": {
         "type": "http",
         "url": "http://192.168.1.100:9500/mcp"
       }
     }
   }
   ```
3. Connect agents - they'll automatically join the collab room

### Both Teams Work Together

- Shared messaging via dead-drop
- Team-scoped names (gypsy.juno, striker.chief)
- Cross-team collaboration
- Neural handshake with all agents

### Done

```bash
# Host shuts down
docker-compose down

# Archive collab data
cp -r collab-data ~/collab-archive/project-$(date +%Y%m%d)
```

## Setup

### Installation

```bash
# Clone or navigate to dead-drop-collab
cd ~/dead-drop-collab

# (Optional) Customize
cp .env.example .env
# Edit .env: set ROOM_NAME, HOST_NAME, GUEST_NAME

# Start
./start.sh
```

### Configuration

**Environment variables (in docker-compose.yml or .env):**
- `ROOM_NAME` - Name shown in banner (default: "project-collab")
- `HOST_NAME` - Host's name (default: "Jesse")
- `GUEST_NAME` - Guest's name (default: "Andrew")

**Custom startup:**
```bash
ROOM_NAME="my-project" HOST_NAME="Jesse" GUEST_NAME="Andrew" ./start.sh
```

## Connection Methods

### Same Room (Local Network)
**Best for:** Co-located work (office, home, coffee shop)
- Fast, low latency
- Use local IP (192.168.x.x or 10.x.x.x)
- No port forwarding needed
- Example: `http://192.168.1.100:9500/mcp`

### Remote (Over Internet)
**Best for:** Remote collaboration
1. Set up port forwarding on router: `9500 → laptop-ip`
2. Find public IP: `curl ifconfig.me`
3. Share public IP with guest
4. Guest connects: `http://your-public-ip:9500/mcp`

**Security note:** Public IP exposes the port to the internet. Use Tailscale/Zerotier for secure tunnels instead.

### Secure Tunnel (Recommended for Remote)
**Best for:** Secure remote collaboration

Using Tailscale:
```bash
# Install Tailscale (one-time)
brew install tailscale
tailscale up

# Share Tailscale hostname
# Guest connects to: http://your-hostname.tailscale.net:9500/mcp
```

Using Zerotier:
```bash
# Install Zerotier (one-time)
brew install zerotier-one

# Join network, share IP
# Guest connects to: http://zerotier-ip:9500/mcp
```

## Workflow

### Typical Session

**Before collaboration (both teams working solo):**
```
Jesse's agents → localhost:9400 (their local dead-drop)
Andrew's agents → localhost:9400 (his local dead-drop)
```

**Starting collaboration:**
```
1. Jesse: ./start.sh (starts container on :9500)
2. Jesse: Sends URL to Andrew
3. Andrew: Updates ~/.mcp.json to Jesse's IP
4. Both: Restart Claude Code (or reload MCP)
5. Agents connect to shared room
```

**During collaboration:**
```
Jesse's agents → localhost:9500 (Jesse's container)
Andrew's agents → 192.168.1.100:9500 (Jesse's container)

Both teams see:
- gypsy.juno, gypsy.spartan, gypsy.cortana
- striker.chief, striker.arbiter, striker.halsey
```

**Ending collaboration:**
```
1. Archive work: cp -r collab-data ~/archive/
2. Jesse: docker-compose down
3. Andrew: Switch ~/.mcp.json back to localhost:9400
4. Both: Back to solo work
```

## Team Scoping

### Why Team-Scoped Names?

**Problem:** Both teams might have agents with the same name (e.g., both have "juno")

**Solution:** Automatic team prefixing in collab rooms

**In solo work (team rooms):**
- Jesse's agents: `juno`, `spartan`, `cortana`
- Andrew's agents: `chief`, `arbiter`, `halsey`

**In collab room:**
- Jesse's agents: `gypsy.juno`, `gypsy.spartan`, `gypsy.cortana`
- Andrew's agents: `striker.chief`, `striker.arbiter`, `striker.halsey`

### Messaging

**Solo mode:**
```python
send(to_agent="spartan", message="Build the API")
```

**Collab mode:**
```python
# Message your own team
send(to_agent="gypsy.spartan", message="Build the API")

# Message other team
send(to_agent="striker.chief", message="Let's coordinate")
```

## Data Storage

**Location:** `~/dead-drop-collab/collab-data/`

**Contents:**
- `messages.db` - All messages between both teams
- `tasks.db` - Task tracking
- `*.log` - Server logs

**Archival:**
```bash
# After collaboration
cp -r collab-data ~/collab-archive/project-$(date +%Y%m%d)

# Or compress
tar -czf project-$(date +%Y%m%d).tar.gz collab-data/
```

## Troubleshooting

### Guest can't connect

**Check firewall:**
```bash
# macOS
System Preferences → Security & Privacy → Firewall → Allow port 9500

# Linux
sudo ufw allow 9500
```

**Verify network connectivity:**
```bash
# From guest laptop
ping 192.168.1.100
telnet 192.168.1.100 9500
```

**Check container is running:**
```bash
# On host
docker ps
# Should show: collab-room container on port 9500
```

### IP address keeps changing

**Solution 1:** Static IP on router
- Log into router admin panel
- Assign static IP to host's MAC address

**Solution 2:** Tailscale/Zerotier
- Use hostname instead of IP
- Hostname stays the same even if IP changes

### Container won't start

```bash
# Check logs
docker-compose logs

# Rebuild from scratch
docker-compose down
docker-compose build --no-cache
docker-compose up
```

### Port 9500 already in use

```bash
# Check what's using it
lsof -i :9500

# Use different port
# Edit docker-compose.yml: "9501:9500"
```

## Benefits vs. Central Hub

### P2P Advantages
✅ No server to maintain
✅ Ad-hoc collaboration (spin up when needed)
✅ Host controls the room (your laptop, your rules)
✅ Works offline on local network
✅ No infrastructure costs
✅ Simple setup (one Docker container)

### P2P Limitations
❌ Host's laptop must stay on
❌ Requires network connectivity to host
❌ IP might change (use Tailscale to solve)
❌ Manual MCP config updates

### When to Use P2P
- **Short-term collaboration** (few hours to few days)
- **Small teams** (2-3 people)
- **Same location** (office, home, co-working space)
- **Informal projects** (hackathons, experiments)

### When to Use Central Hub
- **Long-term collaboration** (weeks to months)
- **Large teams** (4+ people)
- **Remote teams** (different locations, time zones)
- **Production systems** (always-on, high availability)

## Security Considerations

### On Local Network (Safe)
- Machines are on same WiFi/LAN
- Traffic doesn't leave the local network
- Low security risk

### Over Internet (Use Encryption)
**DON'T:** Expose port directly to internet
- Unencrypted traffic
- No authentication
- Anyone with IP can connect

**DO:** Use encrypted tunnel
- Tailscale (recommended)
- Zerotier
- VPN
- SSH tunnel

### Authentication (Future Enhancement)
Current system has no authentication. Anyone with the URL can connect.

**Planned:** Room tokens
- Host generates token when creating room
- Guest needs token to connect
- Token expires after session

## Next Steps

### Immediate
1. Test basic connectivity (same network)
2. Try cross-team messaging
3. Run a small project together

### Future Enhancements
1. **Authentication** - Room tokens for secure access
2. **Dynamic config switching** - No manual MCP edits
3. **Session management** - Auto-detect active rooms
4. **Encrypted transport** - Built-in TLS/SSL
5. **Room templates** - Pre-configured room setups

## Files

```
~/dead-drop-collab/
├── Dockerfile              # Container definition
├── docker-compose.yml      # One-command startup
├── start-collab.py         # Startup script (shows join info)
├── start.sh                # Quick launcher
├── requirements.txt        # Python dependencies
├── README.md              # Full documentation
├── QUICK_START.md         # 3-step guide
├── .env.example           # Config template
└── src/dead_drop/         # Dead-drop server code
```

## Example Session

```bash
# Jesse starts collab room
cd ~/dead-drop-collab
./start.sh

# Container displays:
# HOST (Jesse): http://localhost:9500/mcp
# GUEST (Andrew): http://192.168.1.100:9500/mcp

# Jesse sends to Andrew (Slack/Discord/whatever):
"Join the collab room: http://192.168.1.100:9500/mcp"

# Andrew updates ~/.mcp.json
# Both teams' agents connect

# Work together
# - gypsy.juno and striker.chief coordinate
# - gypsy.spartan builds backend
# - striker.arbiter builds frontend

# Done
docker-compose down
cp -r collab-data ~/archive/project-feb20

# Back to solo work
```

---

**P2P Collaboration - Simple, Fast, No Server Required** 🚀
