# Deploying Flight Watch

Flight Watch runs as two Docker containers from one image on any Linux machine with
about 700 MB of free memory and a little disk (the database grows by tens of MB a
year). It must run **from a home internet connection**: Google treats requests
from cloud and data-centre addresses as bots and blocks them within a few
requests. To be reached from outside your network it needs HTTPS in front of it: a
reverse proxy (Caddy, nginx, Traefik) or a tunnel (Cloudflare Tunnel, Tailscale
Funnel). It needs no login service of its own.

| Container | Does |
|---|---|
| `api` | The site and `/api`, on host port 8091. Reads prices; writes only what is tracked, "check now" jobs and owner sessions |
| `worker` | Collects on its own: small batches of Google Flights searches under a daily request budget, the daily calendar sweep, deal feeds, alerts, health checks, a daily canary search, nightly backups |

[`deploy-notes-home.md`](deploy-notes-home.md) is one complete example: a mini PC at
home behind Cloudflare Tunnel.

## 1. Get the code and settings

```bash
git clone https://github.com/alaurin10/flight-tracking.git /opt/flight-tracking
```
```bash
cd /opt/flight-tracking && cp .env.example .env && mkdir -p data
```

Edit `.env` (every setting is explained in the file):

| Setting | What to put |
|---|---|
| `OWNER_PASSWORD` | A long password (12+ characters) that unlocks editing. Empty: the site is read-only and what is tracked is loaded with `import-config` |
| `TZ` | Your timezone, e.g. `America/Los_Angeles`. The daily sweep and the nightly jobs run on local time |
| `HOME_AIRPORT` | Your airport, e.g. `SEA`: the default origin, and what `doctor` tests from |
| `PUBLIC_URL` | The site's address, e.g. `https://flights.example.com` |
| `TRUSTED_PROXY` | `1` if a reverse proxy or Cloudflare sits in front (so rate limits see real visitor addresses) |

Tunables (search filters, request budget, pacing, alert thresholds, retention,
default deal feeds) are in `config/flighttrack.yaml`, not `.env`.

The containers run as uid 1000. If that isn't your user, run
`sudo chown -R 1000:1000 data`.

## 2. Start it

```bash
GIT_COMMIT=$(git rev-parse --short HEAD) docker compose up -d --build
```

Both services should say `(healthy)` in `docker compose ps` within a minute. The
worker creates `data/flights.db` when it starts.

**What to track.** Open the site's **Manage** page, sign in with `OWNER_PASSWORD`,
and add trips, flexible searches, deal watches and feeds there. A new trip's first
price arrives within a minute or so. To load many at once instead, write them in a
YAML file shaped like `config/seed.example.yaml`, keep it outside the repository (it
is public), and load it:

```bash
docker compose run --rm -v "$PWD/../my-trips.yaml:/tmp/trips.yaml:ro" worker flighttrack import-config /tmp/trips.yaml
```

The owner endpoints under `/api/admin/` already work for scripts (see "Everyday
tasks").

**First-run live checks**, once, from the machine's home connection. Each makes
real requests to Google, so run them a few minutes apart and only these:

```bash
docker compose run --rm worker flighttrack doctor
```
```bash
docker compose run --rm worker flighttrack doctor --calendar
```
```bash
docker compose run --rm worker flighttrack calibrate --count 20 --interval 6 --yes
```

`doctor` makes one results-page request and checks the transport, the page, the
parser and the price units; compare its dollar amounts with google.com/travel/flights
for the same search. `doctor --calendar` adds one calendar request; if its prices
look right, set `calendar.enabled: true` in `config/flighttrack.yaml`.
`doctor --explore anywhere` adds one Explore request and lists the places it returned
and the few with a fare (expect a handful; see `docs/data-sources.md`). You do not need to
set a request budget: the worker learns its pace from how Google answers, starting at 50
a day and rising after clean days up to `collect.pace.max_per_day` (720). `calibrate`
measures a short burst; `collect.daily_request_budget` pins the pace to a fixed number if
you ever want that instead. The Status page shows the pace and why it is where it is.

Check it: `curl -s http://localhost:8091/api/health` says `"status":"ok"` once the
first prices are stored (a few minutes after the first trip is added), and
`http://<host>:8091/` shows the page.

## 3. Put HTTPS in front

Point your proxy or tunnel at `http://<host>:8091`. With Caddy, for example:

```
flights.example.com {
	reverse_proxy localhost:8091
}
```

Then set `PUBLIC_URL` and `TRUSTED_PROXY=1` in `.env` and run
`docker compose up -d`.

In production (`ENV=prod`, which `docker-compose.yml` sets) the owner cookie is
HTTPS-only, so sign in through the HTTPS address, not `http://<host>:8091`. The site
is public and read-only for everyone else; it sends `X-Robots-Tag: noindex` and a
`robots.txt` that disallows everything, so it stays out of search results.

## 4. Monitoring

There is no outside monitor, by choice. Three signals:

- **The status page.** `/api/status` (and the Collector section of the page; the
  app's Status page from milestone 6): health problems in plain words, the last
  successful price, the next batch, any cooldown after a block, the request budget
  used, requests per day by outcome, the deal feeds, the canary and the backups.
- **`/api/health`** answers 503 when the newest price is older than
  `health.max_hours_without_success` (36 h) or the worker's heartbeat has stopped.
  Point an uptime checker at it if you want one.
- **Health notifications.** The worker records a notification when collecting
  stops, and another when it recovers; they are shown on the site and pushed to
  your devices once push is set up (below).

### Push alerts on your phone and computer

1. Make the keys once, and paste the two lines it prints into `.env` with your
   `CONTACT_EMAIL` (push services use it to reach you; it is not shown anywhere):
   ```bash
   docker compose run --rm worker flighttrack push keys
   ```
   Then `docker compose up -d`. Never change the keys afterwards: every device would
   have to turn alerts on again.
2. On each device: open the site, sign in, More → Alerts, choose what to get (price
   alerts, deals, collector trouble), Turn on alerts on this device, then Send a test.
3. iPhone and iPad: add the site to the Home Screen first (Share → Add to Home Screen),
   open it from there, and turn alerts on in that app. It needs iOS 16.4 or later.

Without the keys nothing is pushed; alerts are still recorded and shown on the overview.
At most 20 devices; a device the push service reports gone is removed by itself.

`docker compose ps` shows both services' container health (the API's process, the
worker's heartbeat).

## 5. Automatic updates

`deploy/deploy.sh` fetches `main` and rebuilds when app files changed. To run it
every 5 minutes:

```bash
sudo cp deploy/flightwatch-deploy.service deploy/flightwatch-deploy.timer /etc/systemd/system/
```
```bash
sudo sed -i "s/REPLACE_WITH_YOUR_USER/$USER/" /etc/systemd/system/flightwatch-deploy.service
```
```bash
sudo systemctl daemon-reload && sudo systemctl enable --now flightwatch-deploy.timer
```

Keep that checkout free of local edits (it only fast-forwards). Watch it with
`journalctl -u flightwatch-deploy -f`. If the repository lives somewhere other than
`/opt/flight-tracking`, change `ExecStart` in the service file.

## Everyday tasks

**Change what is tracked:** edit your YAML file and load it again (it creates and
updates by name and never deletes; set `status: paused` or `archived` to stop
something), or export what is tracked now:

```bash
docker compose run --rm worker flighttrack export-config --out /data/tracked.yaml
```

From a script, sign in and call the owner endpoints, sending this site's `Origin`:

```bash
curl -c jar -H 'Origin: https://flights.example.com' -H 'Content-Type: application/json' \
  -d '{"password":"…"}' https://flights.example.com/api/auth/login
```
```bash
curl -b jar -H 'Origin: https://flights.example.com' -H 'Content-Type: application/json' \
  -d '{"name":"Lisbon","origin":"SEA","destination":"LIS","depart_date":"2031-10-03","return_date":"2031-10-13","target_price":65000}' \
  https://flights.example.com/api/admin/trips
```

Prices are in cents. A new or changed trip queues a fetch, so its first price
arrives within a few minutes unless the worker is in a cooldown.

**Change the owner password:** edit `.env`, run `docker compose up -d`, then end
the existing sessions:

```bash
docker compose run --rm worker flighttrack owner logout-all
```

**Logs:** `docker compose logs -f worker` and `docker compose logs -f api` (one JSON
object per line).

**Is it working, in the terminal:** `docker compose exec worker flighttrack status`
and `docker compose exec worker flighttrack health`.

**Book or wait:** `docker compose exec worker flighttrack advise`.

## Backups and restore

Every night at 03:30 local time the worker writes `data/backups/flights-YYYYMMDD.db`
(a consistent copy of the database) and `data/backups/tracked-YYYYMMDD.yaml` (what is
tracked, in the `import-config` shape), keeping 14 of each. Copy `data/backups` off
the machine too: the price history cannot be collected again.

To restore:

```bash
docker compose stop
```
```bash
cp data/backups/flights-20270120.db data/flights.db && rm -f data/flights.db-wal data/flights.db-shm
```
```bash
docker compose up -d
```

## Rolling back an update

```bash
git log --oneline -5
```
```bash
sudo systemctl stop flightwatch-deploy.timer && git checkout <good-commit> && docker compose up -d --build
```

When the fix is on `main`: `git checkout main && sudo systemctl start flightwatch-deploy.timer`.

Migrations only add tables and columns, so an older version normally runs on a newer
database. If it doesn't, restore the backup from before the update as well (see
above); the worker logs "applied database migrations" when an update brings one.

## Troubleshooting

| Symptom | What to check |
|---|---|
| `/api/health` says `no_data` | Nothing is tracked yet, or the worker has not fetched yet. `docker compose logs worker`; the first batch goes 1–5 minutes after start |
| `/api/health` says `stale` | No price stored for 36 h. `docker compose exec worker flighttrack status`: a cooldown after blocks, the budget spent, or every request failing |
| `/api/health` says `worker_stopped` | The worker's heartbeat is over three minutes old: `docker compose ps`, `docker compose logs worker` |
| `in cooldown until …` | Google blocked a request; the worker waits (1 h, then 4, 12, 24), cuts its pace to 60% of what it had sent, and carries on by itself. Frequent blocks: lower `collect.pace.max_per_day` |
| Explore lane "paused" on the status page | Google answered Explore calls without places. Detail and calendar go on; the lane retries after 30 min, 2, 6 and 24 h |
| Calendar lane "paused" on the status page | Google answered calendar calls without prices (it has done so since 2026-08 for some clients). Detail fetches go on; the lane retries after 30 min, 2, 6 and 24 h. A warning appears after a day |
| `layout_drift` on the status page | The daily canary search no longer looks right: Google may have changed the page. The page is saved in `data/failures/`; run `doctor` |
| Owner sign-in doesn't stick | Sign in through the HTTPS address. If a proxy rewrites the Host header, set `PUBLIC_URL` |
| "Too many wrong passwords" | Wait (the lockout doubles each time, up to an hour) or restart the api container |
| Sign-in says it is not set up | `OWNER_PASSWORD` is empty or shorter than 12 characters; check `docker compose logs api` |
| `429 too many requests` | Public reads are limited to 60 a minute per visitor (30 for the airport search). Behind a proxy, set `TRUSTED_PROXY=1` so visitors aren't counted as one |
| Permission denied on `data/` | `sudo chown -R 1000:1000 data` |
| Disk filling up | `docker image prune -f`; the worker prunes old runner-up offers and logs nightly |
