# QR Manager

Self-hosted dynamic QR code generator. Each QR code encodes a stable
`https://your-domain/r/<code>` URL that redirects to a target URL you can
change any time — the printed/exported QR image never has to change.

## Features
- Library view of every code, with live preview
- Create codes with an auto-generated or custom short slug
- Edit the target URL at any time without touching the QR image
- Export any code as PNG or SVG
- Scan counter per code
- Single admin password for the web UI (no multi-user accounts — this is a small internal tool)
- REST API with its own key, for bulk-creating codes from another platform

## Local test run

```bash
cd qr-manager
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
export BASE_URL=http://localhost:5000
export ADMIN_PASSWORD=devpassword
python app.py
```

Visit http://localhost:5000, log in, and create a code.

## Deploying — two options

Either way, UGOS Project Manager only accepts Compose files with an `image:`
reference — it won't run `build:` — so the image has to be built somewhere
else first. Pick whichever fits:

### Option A: GHCR + GitHub Actions (same pattern as sidecar-uploader)

1. Push this repo to `github.com/sidecarprod/qr-manager` (public, or add an
   image pull secret on the NAS if private).
2. `.github/workflows/build.yml` builds and pushes
   `ghcr.io/sidecarprod/qr-manager:latest` on every push to `main`. Set the
   GHCR package to **Public** the first time it's pushed.
3. Deploy `docker-compose.yml` (below).

### Option B: No GitHub — build locally, ship straight to the NAS

Nothing leaves your machine except the image itself, over SSH.

1. On your own machine, with Docker installed, `cd` into this project folder.
2. Run `./deploy.sh` (edit the `NAS_HOST`/`NAS_DIR` variables at the top
   first if needed). It will:
   - `docker build` the image locally
   - `docker save` it to a tarball
   - `scp` the tarball to `/volume2/docker/qr-manager` on SidecarNAS
   - SSH in and `sudo docker load` it (your `Sidecarprod` SSH user isn't in
     the docker group yet, so this uses `sudo`)
   - restart the `qr-manager` container if it's already running
3. The **first** time only, deploy `docker-compose.local.yml` (not
   `docker-compose.yml`) via UGOS Project Manager — it references
   `qr-manager:latest` as a plain local image tag instead of a GHCR one, so
   UGOS won't try to pull it from anywhere.
4. For every update after that, just re-run `./deploy.sh` and restart the
   container from the UGOS UI (or let the script's `docker restart` do it —
   that works without needing UGOS to "redeploy", since the image tag
   doesn't change, only its contents).

Either option, before going live, edit in the compose file:
- `BASE_URL` → the public URL you'll expose via Cloudflare Tunnel (e.g.
  `https://qr.sidecarprod.com`) — this is baked into every QR code, so set
  it before creating real codes.
- `ADMIN_PASSWORD` → a real password for the web UI.
- `SECRET_KEY` → any long random string (`openssl rand -hex 32`).
- `API_KEY` → a long random string if you want the bulk API enabled (leave
  it unset/blank to disable the API entirely).
- Create `/volume2/docker/qr-manager` (or `qr-manager-data` for the local
  variant) on the NAS for the SQLite volume, and point a Cloudflare Tunnel
  hostname at the container's port `5000` (mapped to host `5055`).

## Bulk API

Every endpoint needs the key in a header: `X-API-Key: <API_KEY>` (or
`Authorization: Bearer <API_KEY>`). If `API_KEY` isn't set, the whole `/api/*`
surface returns 403.

**Create one code**
```bash
curl -X POST https://qr.sidecarprod.com/api/codes \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"target_url": "https://example.com/session1", "label": "Session 1"}'
```

**Bulk create** (up to 500 per request; each item can optionally set its own
`custom_code`; partial failures don't block the rest of the batch)
```bash
curl -X POST https://qr.sidecarprod.com/api/codes/bulk \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{
    "codes": [
      {"target_url": "https://example.com/a", "label": "Room A"},
      {"target_url": "https://example.com/b", "label": "Room B"}
    ]
  }'
```
Response includes each item's `success`/`error` and, on success, the same
object shape as a single create — including `redirect_url`, `png_url`, and
`svg_url` so the calling platform can immediately fetch the image.

**Other endpoints**
- `GET /api/codes` — list everything
- `GET /api/codes/<id>` — one code
- `PATCH /api/codes/<id>` — update `target_url` and/or `label`
- `DELETE /api/codes/<id>` — delete
- `GET /api/codes/<id>/qr.png` / `.../qr.svg` — download the image directly

## Notes
- Data lives in a single SQLite file at `/data/qr.db` inside the volume —
  back it up the same way you back up your other `/volume2/docker/*`
  appdata.
- If you ever change `BASE_URL` after codes already exist, every already
  *printed* QR code still points at the old domain — only newly generated
  images use the new `BASE_URL`. Treat `BASE_URL` as fixed once you're live.
- There's no built-in HTTPS — Cloudflare Tunnel terminates TLS, same as
  your other public subdomains.
