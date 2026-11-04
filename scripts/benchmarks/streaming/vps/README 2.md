# VPS-shaped streaming benchmark

Reproduces the capacity and recovery measurements in `docs/deployment-contabo.md` (section 13).
Run it only against an **isolated** stack: its own database, Redis DBs and API port. Never point it
at production.

## What it measures

- `bench_vps.mjs`: N WebRTC viewers (`whep-load`, pion, no decoding) on the same Docker network as
  the MediaMTX under test, while one headless Chromium viewer measures glass-to-glass latency by
  reading a timestamp painted into every frame. Per level: joins, first packet, received bitrate,
  RTP loss, seconds without packets, server egress, MediaMTX CPU and memory.
- `reconnect.spec.ts` (Playwright): the real player app; broadcaster outage and MediaMTX restart;
  time until frames are presented again and the number of reconnect attempts.

## Set-up (workstation, Docker)

1. An isolated API (e.g. `:8016`) with `MEDIAMTX_API_URL=http://127.0.0.1:19997`,
   `MEDIAMTX_API_PASSWORD`, and a tenant with the `admin` / `player1` accounts
   (Playwright's global setup creates one). Run Celery worker **with beat** for the reconnect test.
2. Network and two MediaMTX containers from `infrastructure/mediamtx/mediamtx.yml`:

   ```bash
   docker network create esabong-bench-net
   # Server under test: production limits, port mappings for the API and the Chromium viewer
   docker run -d --name esabong-bench-mediamtx --network esabong-bench-net --cpus 2 --memory 2g --cap-add NET_ADMIN \
     -e MTX_AUTHHTTPADDRESS=http://host.docker.internal:8016/api/v1/internal/mediamtx/auth \
     -e MTX_WEBRTCIPSFROMINTERFACES=true -e MTX_WEBRTCADDITIONALHOSTS=127.0.0.1 \
     -p 8189:8189/udp -p 8189:8189/tcp -p 127.0.0.1:18888:8888 -p 127.0.0.1:18889:8889 -p 127.0.0.1:19997:9997 \
     -v "$PWD/infrastructure/mediamtx/mediamtx.yml:/mediamtx.yml:ro" bluenviron/mediamtx:1.21.3-ffmpeg
   # 300 Mbit/s egress cap like the Contabo port
   docker exec esabong-bench-mediamtx sh -c 'apk add -q iproute2-tc && tc qdisc add dev eth0 root tbf rate 300mbit burst 512kb latency 50ms'
   # Origin: receives the timestamped canvas (WHIP) and re-encodes it OBS-like to the server under test
   docker run -d --name esabong-bench-origin --network esabong-bench-net \
     -e MTX_AUTHHTTPADDRESS=http://host.docker.internal:8016/api/v1/internal/mediamtx/auth \
     -e MTX_WEBRTCIPSFROMINTERFACES=false -e MTX_WEBRTCADDITIONALHOSTS=127.0.0.1 \
     -e MTX_WEBRTCLOCALUDPADDRESS=:28189 -e MTX_WEBRTCLOCALTCPADDRESS= -e MTX_HLS=no \
     -p 28189:28189/udp -p 127.0.0.1:28889:8889 \
     -v "$PWD/infrastructure/mediamtx/mediamtx.yml:/mediamtx.yml:ro" bluenviron/mediamtx:1.21.3-ffmpeg
   ```
3. Build the viewer client: `docker build -t esabong-bench-whepload scripts/benchmarks/streaming/vps/whep-load`

## Run

```bash
node scripts/benchmarks/streaming/vps/bench_vps.mjs <tenant-slug> /tmp/load.jsonl 5 10 25 50 100
BITRATE=1500k node scripts/benchmarks/streaming/vps/bench_vps.mjs <tenant-slug> /tmp/load.jsonl 100
```

Reconnect test: make a stream the tenant's enabled default, rotate its key, then copy
`reconnect.spec.ts` into `web/e2e/` temporarily and run it with `E2E_SKIP_SEED=1`,
`E2E_TENANT_SLUG=<slug>`, `BENCH_OBS_KEY=<id>?key=<key>` and the isolated `E2E_*` URLs
(see `docs/testing.md`). Remove the copy afterwards.

Clean up: `docker rm -f esabong-bench-mediamtx esabong-bench-origin && docker network rm esabong-bench-net`.
