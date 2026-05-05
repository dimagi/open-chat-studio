# Cloudflare Zero Trust - Administration

This guide covers ongoing administration of a self-hosted Open Chat Studio instance that uses Cloudflare Zero Trust for access control: managing users, devices, sessions, and audit logs.

!!! note "Self-hosted deployments only"
    This guide applies only to self-hosted Open Chat Studio instances. If you are using the hosted version at openchatstudio.com, or a deployment that uses a reverse proxy without Cloudflare, this does not apply.

For the initial setup, Docker, tunnel token, and tunnel ingress configuration, see the [Cloudflare Tunnel setup guide](./cloudflare_tunnel.md).

---

## Adding a new user

### Step 1: Collect their device serial number

Ask the team member to find their device serial number:

- **macOS**: Apple menu → About This Mac → Serial Number
- **Windows**: Start → type `cmd` → run `wmic bios get serialnumber`
- **Linux**: run `sudo dmidecode -s system-serial-number`

### Step 2: Register their device

1. Go to **Settings → WARP Client → Device posture**.
2. Open your serial number rule.
3. Add their serial number.
4. Click **Save**.

### Step 3: Add their email to the Access policy

1. Go to **Access → Applications → Open Chat Studio → Policies**.
2. Edit your Allow policy.
3. Under **Include**, add their email address.
4. Click **Save**.

Access is granted immediately once both the device and email are registered.

### Step 4: Send them the WARP setup instructions

Ask the team member to install the [Cloudflare WARP client](https://developers.cloudflare.com/cloudflare-one/connections/connect-devices/warp/download-warp/) and then follow these steps:

1. Open WARP → **Preferences → Account → Login with Cloudflare Zero Trust**
2. Enter the organisation team name (available under **Zero Trust → Settings → Custom Pages**)
3. Enter the **OTP code** sent to their work **email**
4. Turn **WARP ON** - the icon should show **Connected**
5. Open a browser and navigate to the app **hostname** (e.g. `https://ocs.your-org`)

---

## Removing a user

**To remove access immediately:**

1. Go to **Access → Applications → Open Chat Studio → Policies**.
2. Remove their **email address** from the Include list.
3. Click **Save**.

They are blocked from the next request onwards.

**To revoke any currently active session:**

1. Go to **Zero Trust → My Team → Users**.
2. Find the user and click **Revoke active sessions**.

This terminates their Access token immediately rather than waiting for it to expire.

**To remove their device:**

1. Go to **Settings → WARP Client → Device posture**.
2. Remove their serial number from the rule.
3. Click **Save**.

!!! warning "Departing staff"
    Always do both steps, remove the email from the Access policy **and** revoke active sessions, when a team member leaves. Removing the email alone prevents new logins but does not immediately terminate an existing session.

---

## Device replacement

When a team member gets a new device:

1. Collect the new device's serial number (see [Step 1](#step-1-collect-their-device-serial-number) above).
2. Go to **Settings → WARP Client → Device posture** and edit your rule.
3. Remove the old serial number and add the new one.
4. Click **Save**.
5. Ask the user to install WARP on the new device and re-enrol.

Their email assignment in the Access policy does not change, only the device registration updates.

---

## Session settings

| Setting | Default | Adjustable range |
|---|---|---|
| App session (OTP re-auth) | 8 hours | 4–24 hours |
| WARP session (network) | 24 hours | 8–168 hours |

**To change the app session duration:**
Go to **Access → Applications → Open Chat Studio → Session duration**.

**To change the WARP session duration:**
Go to **Settings → WARP Client → Global settings → Session duration**.

!!! warning "Keep WARP session longer than app session"
    The WARP session must always be set equal to or longer than the app session. If WARP expires before the app session, users will be disconnected from the network while the application still considers them logged in, causing confusing errors.

!!! note "Profile changes require WARP reconnect"
    Changes to Split Tunnels, Local Domain Fallback, or device posture rules are not pushed to connected WARP clients in real time. Users must disconnect and reconnect WARP to pick up the updated profile. Consider asking all users to toggle WARP after making profile changes.

---

## Reading audit logs

Cloudflare maintains two separate logs.

### Access logs - who connected and whether they were allowed

**Location:** Zero Trust → Logs → Access

Each entry shows the user email, device serial, source IP, country, allow/deny decision, and timestamp. Use this log to:

- Confirm a user connected successfully
- Investigate a failed access attempt
- Check whether a removed user attempted access after revocation

### Gateway logs - network activity

**Location:** Zero Trust → Logs → Gateway

Each entry shows which private addresses were reached, how much data was transferred, and which device made the request. Use this to monitor usage or investigate unusual activity.

### Exporting logs

For long-term storage or compliance reporting:

1. Go to **Zero Trust → Logs → Logpush**.
2. Click **Add destination**.
3. Choose a destination: AWS S3, Splunk, Datadog, or Cloudflare R2.
4. Select Access logs, Gateway logs, or both.

!!! tip "Retention limits"
    Dashboard log retention is up to 6 months. Setting up Logpush removes that limit and is recommended for organisations with compliance or audit requirements.

---

## Common situations

### A user cannot connect despite being set up

Work through this checklist:

1. Is **WARP** installed and turned on? The icon should be blue and show **Connected**.
2. Has the user enrolled their device in the organisation? **(WARP → Settings → Account → Login with Cloudflare Zero Trust)**
3. Is their serial number in the device posture rule?
4. Is their email address in the Access policy?
5. Is the tunnel healthy? Go to **Networks → Tunnels**; status should show **Healthy**.
6. Can they reach the app by **CIDR IP**? Try `http://172.18.0.7:8000`. If this works but the hostname doesn't, the issue is **DNS**, not access.
7. Is Local Domain Fallback configured with the correct dnsmasq IP? Go to **Settings → WARP Client → Device profiles → Default profile → Local Domain Fallback**.
8. Is Gateway Proxy enabled with **UDP**? Go to **Traffic policies → Traffic settings → Proxy and inspection**. UDP must be on for DNS resolution to private IPs.

If all of the above are correct, check the Access logs. The deny reason will identify exactly which check is failing.

### A user sees `DNS_PROBE_FINISHED_NXDOMAIN` when accessing the private hostname

The private hostname cannot be resolved. This is a DNS issue, not an access issue. Check in order:

1. Is **WARP connected**? Private hostnames only resolve through WARP.
2. Has the user toggled **WARP OFF** and on since the last profile change? Profile updates require a reconnect.
3. Is Local Domain Fallback configured? Go to **Settings → WARP Client → Device profiles → Default profile → Local Domain Fallback**; the hostname must be listed with the dnsmasq IP.
4. Is Gateway Proxy enabled with UDP? Go to **Traffic policies → Traffic settings**; UDP must be on.
5. Is the dnsmasq container running? Check on the server: `docker compose -f docker-compose.cloudflare.yml logs dns`
6. Is the hostname using `.local`? The `.local` TLD is reserved for mDNS and will not resolve through WARP. Use a different name.

### A device is lost or stolen

Act immediately:

1. Go to **Settings → WARP Client → Device posture** and remove the serial number.
2. Go to **Zero Trust → My Team → Users** and revoke active sessions for that user.
3. Check Access logs for any recent connections from that device.

Access is revoked as soon as the serial number is removed.

### The tunnel is down and all users cannot connect

The tunnel runs with `restart: unless-stopped` - most crashes recover within seconds. If it has been unavailable for more than a minute:

1. Check the tunnel status at **Networks → Tunnels**.
2. If the status shows **Unhealthy** or **Inactive**, restart the `cloudflared` container:

```bash
docker compose -f docker-compose.prod.yml -f docker-compose.cloudflare.yml restart cloudflared
```

### A user's OTP email is not arriving

1. Ask them to check their **spam or junk folder**.
2. Confirm their email address in the Access policy is spelled correctly.
3. Ask them to click **Resend code** and wait 60 seconds.
4. If the problem persists, remove their email from the policy and re-add it to reset their Access session.

---

## Quick reference

| Task | Location in dashboard |
|---|---|
| Add device serial | Settings → WARP Client → Device posture |
| Remove device serial | Settings → WARP Client → Device posture |
| Add user email | Access → Applications → [app] → Policies |
| Remove user email | Access → Applications → [app] → Policies |
| Revoke active session | Zero Trust → My Team → Users → [user] → Revoke |
| Check who connected | Logs → Access |
| Check network activity | Logs → Gateway |
| Export logs | Logs → Logpush |
| Check tunnel health | Networks → Tunnels |
| Change app session duration | Access → Applications → [app] → Session duration |
| Change WARP session duration | Settings → WARP Client → Global settings |
| Configure Local Domain Fallback | Settings → WARP Client → Device profiles → Default → Local Domain Fallback |
| Enable Gateway Proxy | Traffic policies → Traffic settings → Proxy and inspection |
| Check dnsmasq status | On server: `docker compose -f docker-compose.cloudflare.yml logs dns` |

---

## Related

- [Cloudflare Tunnel setup guide](./cloudflare_tunnel.md) - initial setup, Docker compose, tunnel ingress, Access policies
- [Zero Trust Access overview](./zero_trust_access.md) - tool comparison and webhook path requirements
