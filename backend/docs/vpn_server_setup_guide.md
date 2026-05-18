# VPN Server Setup Guide (3x-ui + VLESS)

This guide walks through setting up a 3x-ui panel on a VPS with VLESS + WebSocket + TLS.

## Prerequisites

- A VPS with Ubuntu 22.04/24.04 (or Debian 11/12)
- Root access via SSH
- A domain name pointed to the server IP (optional but recommended for TLS)
- Cloudflare account (optional, for TLS via Cloudflare)

## Step 1: Update System

```bash
ssh root@YOUR_SERVER_IP
apt update && apt upgrade -y
apt install -y curl wget socat tar
```

## Step 2: Install 3x-ui

```bash
bash <(curl -Ls https://raw.githubusercontent.com/mhsanaei/3x-ui/master/install.sh)
```

During installation:
- Set admin username (remember it)
- Set admin password (remember it)
- Set panel port (default: 2053, or choose your own)

## Step 3: Configure Panel Access

```bash
# Enable and start 3x-ui
x-ui start
x-ui enable

# Check status
x-ui status
```

Open the panel in browser: `https://YOUR_SERVER_IP:2053`
- Accept self-signed certificate warning
- Login with admin credentials

## Step 4: Add VLESS Inbound

In the 3x-ui panel:

1. Go to **Inbounds** → **Add Inbound**
2. Settings:
   - **Remark**: `vless-ws-tls` (or any label)
   - **Protocol**: `vless`
   - **Listen IP**: (leave empty)
   - **Port**: `443`
   - **Clients**: (leave default — we add clients via API)
   - **Network**: `ws`
   - **Path**: `/ws` (or any path you choose)
   - **TLS**: `tls`
   - **SNI**: `your-domain.com` (if using domain) or server IP
   - **Cert/Key**: If using Cloudflare, use Cloudflare origin cert. If no domain, leave empty.
3. Click **Add**

**Important**: Note the **Inbound ID** from the URL or the inbounds list. You'll need it.

## Step 5: Configure API Access

1. In 3x-ui panel, go to **Panel Settings**
2. Enable **API Access** (if available)
3. The API base URL is: `https://YOUR_SERVER_IP:2053`

API endpoints:
- `POST /login` — authenticate, get session cookie
- `POST /panel/inbound/addClient/{inbound_id}` — add client
- `POST /panel/inbound/updateClient/{inbound_id}` — update client
- `POST /panel/inbound/{inbound_id}/delClient/{client_id}` — delete client

## Step 6: Record Server Details

You'll need these values for the backend configuration:

| Parameter | Example | Where to find |
|---|---|---|
| Panel URL | `https://1.2.3.4:2053` | Your server IP + port |
| Panel Username | `admin` | Set during installation |
| Panel Password | `your-password` | Set during installation |
| Inbound ID | `1` | Inbounds page in panel |
| Server Host | `1.2.3.4` or `vpn.yourdomain.com` | Server IP or domain |
| Server Port | `443` | Configured in Step 4 |
| WS Path | `/ws` | Configured in Step 4 |
| TLS SNI | `vpn.yourdomain.com` | Configured in Step 4 |

## Step 7: Test API Access

```bash
# Login
curl -k -X POST https://YOUR_SERVER_IP:2053/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin&password=your-password" \
  -c cookies.txt

# List inbounds
curl -k -X GET https://YOUR_SERVER_IP:2053/panel/inbound/list \
  -b cookies.txt
```

## Step 8: Configure Firewall

```bash
# Allow panel access (restrict to your backend server IP if possible)
ufw allow 2053/tcp

# Allow VLESS connections
ufw allow 443/tcp

# Enable firewall
ufw enable
```

## Optional: Cloudflare TLS Setup

If using Cloudflare for TLS:

1. Add your domain to Cloudflare
2. Create DNS A record pointing to server IP (enable Cloudflare proxy)
3. In Cloudflare Dashboard → SSL/TLS → Origin Server → Create Certificate
4. Download cert and key, paste into 3x-ui inbound TLS settings
5. Cloudflare SSL mode: Full (Strict)

This gives you free TLS + DDoS protection + hides server IP.

## Multiple Servers

Repeat this guide for each VPN server. Each server gets its own 3x-ui panel.
The backend will connect to each panel's API independently.

## Security Notes

- Change default panel port from 2053
- Use strong admin credentials
- Restrict panel access by IP if possible
- Use HTTPS (self-signed cert or Cloudflare)
- Do not expose panel port in any public configuration
