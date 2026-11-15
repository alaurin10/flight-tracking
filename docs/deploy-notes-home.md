# Deployment notes: the home setup

One real installation of Flight Watch, as an example to adapt: the mini PC at home
(Proxmox, with a Debian Docker VM) that already runs Snow Watch, published at
`https://flights.andrewlaurin.com` through the Cloudflare Tunnel that the Home
Assistant VM runs. No router port is open: the tunnel is outbound only. Requests to
Google leave from the home address, which is what keeps them from being treated as
a data centre.

The general steps are in [`DEPLOYMENT.md`](DEPLOYMENT.md); this file only adds what
is specific to this machine.

## What runs where

| Where | What |
|---|---|
| Docker VM (Debian 12) | `api` (port **8091**) and `worker`, from `/opt/flight-tracking` |
| Same VM, other stacks | Home Hub dashboard on 8080, Snow Watch on 8090 |
| Home Assistant VM | The Cloudflared app, which also runs the tunnel for `ha.`, `dash.` and `snow.` |
| Cloudflare | DNS and the tunnel edge (HTTPS) |

## Machine-specific steps

1. **Code:** the repository is public, so a plain clone works:
   ```bash
   git clone https://github.com/alaurin10/flight-tracking.git /opt/flight-tracking
   ```
2. **Settings:** `cd /opt/flight-tracking && cp .env.example .env`, then in `.env`:
   `OWNER_PASSWORD` (12 or more characters), `PUBLIC_URL=https://flights.andrewlaurin.com`,
   `TRUSTED_PROXY=1`, `TZ=America/Los_Angeles`, `HOME_AIRPORT=SEA`.
3. **Start it and load the trips** (the trips file lives outside the checkout, never
   in the repository):
   ```bash
   GIT_COMMIT=$(git rev-parse --short HEAD) docker compose up -d --build
   ```
   ```bash
   docker compose run --rm -v "$HOME/flight-trips.yaml:/tmp/trips.yaml:ro" worker flighttrack import-config /tmp/trips.yaml
   ```
4. **Live checks from the home address** ([`DEPLOYMENT.md`](DEPLOYMENT.md) step 2):
   `doctor`, `doctor --calendar`, then `calibrate --count 20 --interval 6 --yes`. If
   the measured limit is well above or below 100 requests a day, set
   `collect.daily_request_budget` in `config/flighttrack.yaml` to about half of it
   (at most 80). That file is committed, so make the change in a pull request rather
   than as a local edit, which would stop the deploy timer's fast-forward.
5. **Tunnel:** in Home Assistant → Settings → Apps → Cloudflared → Configuration,
   add to `additional_hosts`, then restart the app:
   ```yaml
   - hostname: "flights.andrewlaurin.com"
     service: "http://<docker-vm-ip>:8091"
   ```
   Do not create a Cloudflare Access application for it: the site is public and
   read-only, and has its own owner sign-in.
6. **Automatic updates:** [`DEPLOYMENT.md`](DEPLOYMENT.md) step 5 (three commands).
7. **Check from outside**, on a phone with Wi-Fi off: the site opens, owner sign-in
   works, and the router still has no port-forward rules.

The Home Assistant VM carries the tunnel, so the site is briefly unreachable from
outside while Home Assistant restarts. Collecting is unaffected: the worker's
requests go straight out.

## Moving over from checkpoint A

If the old container from checkpoint A (the static page on port 8081, LAN only) is
running, its database carries forward; nothing is collected twice.

1. Stop it and keep your edited `config.yaml` somewhere outside the checkout:
   ```bash
   cd /opt/flight-tracking && docker compose down
   ```
   ```bash
   cp config.yaml ~/flight-trips.yaml && sudo cp -a data ~/flight-data-before-m5
   ```
2. Hand the data to uid 1000 (the old image ran as root) and drop the local edits,
   so the deploy timer can fast-forward:
   ```bash
   sudo chown -R 1000:1000 data
   ```
   ```bash
   git checkout -- . && git pull
   ```
3. Continue from step 2 above. `import-config ~/flight-trips.yaml` loads the old
   trips, patterns and deal watches into the database, keeping their names, so the
   price history behind each date pair is unchanged.
4. The old page moves from port 8081 to 8091; remove any bookmark to the old one.
