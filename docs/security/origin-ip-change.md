# Origin IP change and hiding (2026-11-20 DDoS)

The old origin IP was attacked directly twice on 2026-11-20 (05:59 and ~10:20 UTC) and Contabo
suspended it to protect its network. Cloudflare only protects traffic that goes **through**
Cloudflare; the attackers used the raw IP. It was known from DNS history (A records before the
cutover) and from every WebRTC answer (`STREAM_PUBLIC_HOSTS`). A new IP only helps if nothing
reveals it again.

**Never write the new IP into git, docs, chat or tickets other than Contabo's.** Use
`<new-ip>` in notes.

## What reveals an origin IP, and the control for each

| Leak | Control |
|------|---------|
| DNS records pointing at the IP (now or in history) | Cloudflare Tunnel: hostnames are CNAMEs to the tunnel; no A record holds the IP |
| Scanners (Censys/Shodan) reading the site certificate on 80/443 | Gateway ports bound to `127.0.0.1`; visitors arrive only through the tunnel |
| WebRTC answers to players (ICE candidates) | `STREAM_PUBLIC_WEBRTC=false`: players and other tenants' staff get LL-HLS through Cloudflare; MediaMTX refuses their WebRTC reads |
| RTMPS ingest certificate on 1936 | `STREAM_RTMPS_BIND=127.0.0.1` (operators publish from the browser, WHIP); open it only to known operator IPs if OBS is needed |
| Custom-domain instructions shown to tenant admins | `CUSTOM_DOMAIN_GATEWAY_IPS` lists only Cloudflare addresses, never the origin |
| SSH | Allow only admin IPs (host firewall / Contabo) |

## Steps

1. **New IP.** Order an additional IPv4 for the VPS (Contabo panel). Configure it on the
   interface as the **primary** address with the default route using it as source (Contabo's
   additional-IP guide, netplan), so outbound traffic (the tunnel) never leaves from the
   suspended address. Keep the old IP configured until step 6 so SSH still works when it is up.
2. **Tunnel (Cloudflare dashboard).** Zero Trust → Networks → Tunnels → Create (cloudflared),
   name `esabong-origin`, copy the token. Public hostnames, each with service
   `HTTPS` → `gateway:443`, TLS: *Origin Server Name* = the hostname, *No TLS Verify* = on:
   `swcmain183.live`, `www.swcmain183.live` (if used), `uat.swcmain183.live`, `swcmain185.live`,
   `www.swcmain185.live`. Delete each hostname's existing A record first (the dashboard creates
   the tunnel CNAME).
3. **Gateway** (`/opt/esabong/gateway/infrastructure/gateway/.env`, mode 600), then
   `infrastructure/scripts/gateway.sh up`:
   ```
   COMPOSE_PROFILES=tunnel
   CLOUDFLARE_TUNNEL_TOKEN=<token>
   HTTP_PORT=127.0.0.1:80
   HTTPS_PORT=127.0.0.1:443
   GATEWAY_TRUSTED_PROXIES=10.250.250.0/29
   GATEWAY_CLIENT_IP_HEADER=CF-Connecting-IP
   ```
   The gateway must run in dual mode (the uat-only overlay does not attach the tunnel network).
4. **Prod and UAT** (`/opt/esabong/{prod,uat}/.env`), then
   `docker compose up -d api worker mediamtx` in each:
   ```
   STREAM_PUBLIC_WEBRTC=false
   STREAM_PUBLIC_HOSTS=<new-ip>          # only operators/broadcaster staff ever receive it
   STREAM_RTMPS_BIND=127.0.0.1
   CUSTOM_DOMAIN_GATEWAY_IPS=<Cloudflare addresses swcmain185.live resolves to, no origin IP>
   ```
5. **Verify.**
   - `https://swcmain183.live/api/ready`, UAT and `swcmain185.live` answer through Cloudflare.
   - `curl -k --connect-timeout 5 https://<new-ip>/` from outside: refused / timeout.
   - `dig +short swcmain183.live` (and the other hostnames) return only Cloudflare addresses.
   - A player's playback response has `"url": ""` and plays "Live · HLS"; the provider's
     operator/declarator still gets WebRTC.
   - Betting, settlement, WebSocket updates and the declarator desk work on UAT before prod.
6. **Release the old IP** in the Contabo panel once everything runs on the new one, and remove it
   from the interface.

## Rollback

Each layer reverts on its own: tunnel hostnames back to proxied A records (Cloudflare DNS),
`COMPOSE_PROFILES=` and the original `HTTP_PORT`/`HTTPS_PORT` in the gateway `.env`
(`gateway.sh up`), `STREAM_PUBLIC_WEBRTC=true` (`docker compose up -d api worker mediamtx`).
Rolling back the tunnel or ports exposes the IP again.

## Later

Move MediaMTX to a separate small VPS so an attack on media only interrupts video, and players
can return to WebRTC there without exposing the application origin.
