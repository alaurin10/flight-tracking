# Deploying on the Proxmox mini PC — everything you do on your end

This is the runbook for *your* box: the HP EliteDesk 600 G3 Mini (i5-7500T,
16 GB, 128 GB SSD) running Proxmox VE, with the Home Assistant OS VM, the Debian
Docker VM, and the Pi-hole LXC already on it (per `proxmox/README.md` in
home-hub). It follows that repo's rules: extras run as small unprivileged
LXCs, Docker stays in the Docker VM, every guest gets a static IP, nothing
opens an inbound port, Tailscale is for admin, Cloudflare Tunnel + Access is
for browser dashboards, and updates are pulled by a timer, never pushed.

Time on your side: about 30 minutes, most of it waiting for `apt`.

---

## 0. Decide where it lives

| Option | Fits your rules? | Disk | RAM | When to pick it |
|---|---|---|---|---|
| **A. New unprivileged LXC, no Docker** (recommended) | yes — "extras as isolated LXCs", like Pi-hole | ~1.3 GB template + venv, 3 GB thin disk | ~60 MB used, 512 MB cap | You want it isolated, snapshot-able, and its own `flights` host. The scraper's IP-reputation risk stays in its own guest. |
| B. Another compose stack on the Docker VM | yes — "app stacks live here" | ~300 MB image inside the VM's 24 GB | ~80 MB | You'd rather not add a guest. `docker-compose.yml` in the repo root works as-is. |

The rest of this document is option A. Option B is the three lines under §9.

**Not** Docker inside an LXC (your architecture doc rules it out, correctly).

---

## 1. Storage: what it will actually use

Measured, not guessed. The database is a single SQLite file.

| Thing | Size | Notes |
|---|---|---|
| Debian 12 LXC template + Python + venv | ~1.2 GB | one-time |
| Database growth, default config (60 fetches/day × 5 offers) | **~100 MB / year** | every fetch keeps the 5 cheapest itineraries with their raw JSON |
| Database growth with `offers_per_fetch: 1` | ~20 MB / year | if you only ever want the minimum |
| With retention on (default) | **bounded at ~35 MB** | runner-up offers older than 90 days and request logs older than 180 days are pruned weekly and the file is vacuumed; the cheapest-offer series is never pruned |
| Failure artifacts (`data/failures/`) | ≤ 20 pages, typically < 30 MB total | rotating cap |
| The report (`out/index.html`) | ~150–300 KB | rewritten each run |
| Logs | journald | capped by `SystemMaxUse` in `/etc/systemd/journald.conf` |

So: a **3 GB** thin-provisioned rootfs is generous; the container will sit
around 1.5 GB used for years. `flighttrack status` prints the database size;
`flighttrack compact` shows what a prune would remove.

---

## 2. On the Proxmox host: create the LXC (5 min)

Pick a free LAN address and reserve it on the router (you already do this for
every guest). Then, on the host as root:

```bash
curl -fsSLo pct-create.sh https://raw.githubusercontent.com/alaurin10/flight-tracking/main/deploy/lxc/pct-create.sh
CTID=110 IP=192.168.1.60 GW=192.168.1.1 bash pct-create.sh
```

That creates an **unprivileged Debian 12 LXC** named `flights` (1 vCPU,
512 MB RAM, 256 MB swap, 3 GB on `local-lvm`, `onboot=1`) and starts it.
Override `STORAGE=`, `BRIDGE=`, `CTID=` if your names differ; `pvesm status`
lists storages. If you prefer the web UI: Create CT → Debian 12 template →
unprivileged → 1 core / 512 MB / 3 GB → static IP → Start at boot.

Add it to the same **vzdump backup job** as the other guests (Datacenter →
Backup); the whole tracker, history included, is that one 3 GB volume.

## 3. Inside the LXC: install (10 min)

```bash
pct enter 110          # or: ssh root@192.168.1.60 once you've set a password/key
apt-get update && apt-get install -y git
git clone https://github.com/alaurin10/flight-tracking /opt/flighttrack
bash /opt/flighttrack/deploy/lxc/install.sh
```

`install.sh` is idempotent. It installs Python, creates a `flighttrack` system
user, builds the venv with the `[full]` extras (`primp` for a browser TLS
fingerprint, `fast-flights` as the fallback parser), writes `/etc/flighttrack.env`,
and enables two units:

- `flighttrack-serve.service` — the daily job at **03:15 local time** (+ up to
  45 min jitter) and the report server on **:8080**.
- `flighttrack-update.timer` — every 30 min, fast-forward `origin/main`, reinstall,
  restart *only if something changed*. Same poll-and-deploy posture as
  `apps/dashboard/scripts/deploy.sh` in home-hub: outbound-only, no webhook.

Set the container's timezone if it isn't yours (the schedule is local time):

```bash
timedatectl set-timezone America/Los_Angeles
```

## 4. Secrets and config (5 min)

```bash
nano /etc/flighttrack.env          # NTFY_TOPIC=<long random string>
nano /opt/flighttrack/config.yaml  # your trips, patterns, deal watches, targets
systemctl restart flighttrack-serve
```

What to edit in `config.yaml`:

- `trips:` — the Japan dates are placeholders; put your real ones and a target.
- `patterns:` — the ski weekends are Jan–Mar 2027 Fri→Mon and Thu→Mon to SLC.
- `deals.watches` — origins/regions/price ceilings for announced-deal pushes.
- `routes[].target_price` — in cents; guesses until a season of data exists.
- Leave `calendar.enabled: false` until step 5 says otherwise.

`config.yaml` is committed with placeholders, so `git pull` will not overwrite
your edits silently; if upstream ever changes the file, the updater's
`--ff-only` merge fails loudly in `journalctl -u flighttrack-update` and you
resolve it by hand.

## 5. The live checks (the part this project could not do from the sandbox)

Run as the service user so paths and permissions match production:

```bash
cd /opt/flighttrack
alias ft='sudo -u flighttrack .venv/bin/flighttrack --config config.yaml'

ft doctor                 # one real Google Flights request: page kind, parser, price units
ft doctor --calendar      # one calendar RPC; set calendar.enabled: true only if the prices look right
ft run --dry-run          # the whole daily job; alerts printed, not sent
ft calibrate --count 20 --interval 6 --yes   # measure the rate limit before raising fetch.max_queries_per_run
ft status                 # runs, request outcomes, health, database size
```

`doctor` prints raw prices beside normalized cents; compare the dollar
amounts with google.com/travel/flights for the same search. If it reports
`blocked` or `layout`, `docs/data-sources.md` explains what each means and the
raw page is saved under `data/failures/`.

Then trigger the first real run instead of waiting for 03:15:

```bash
curl -X POST http://localhost:8080/run
journalctl -u flighttrack-serve -f
```

## 6. Notifications on your phone (3 min)

You already plan ntfy for Uptime Kuma (home-hub `docs/09-roadmap.md` M1);
this uses the same app.

1. Install **ntfy** (iOS/Android). Subscribe to the topic you put in
   `/etc/flighttrack.env`. Topics on ntfy.sh are public to anyone who guesses
   the name — make it long and random, or point `notify.ntfy.server` at a
   self-hosted ntfy container on the Docker VM later.
2. Test: `ft alert --dry-run` prints what would send; `ft health --notify`
   sends a real push only if the collector is unhealthy.

Three kinds of push, all documented in `docs/using.md`: a price alert, a
matched deal post, and a health warning. Expect the first weeks to be quiet.

## 7. Reaching the report

- **On the LAN:** `http://192.168.1.60:8080/` (bookmark it on the phone).
- **Local name:** add `flights.home.arpa → 192.168.1.60` in Pi-hole (Local DNS
  → DNS Records), matching the N1/N2 plan. When the Caddy LXC lands, add
  `flights.home.arpa` → `192.168.1.60:8080` to the Caddyfile for HTTPS.
- **Away from home, admin-style:** Tailscale. Either the subnet router LXC
  from roadmap N3 (then the LAN URL just works on cellular) or `apt install
  tailscale && tailscale up` inside this LXC.
- **Away from home, browser-style (the way you reach `dash.<domain>`):** add a
  hostname to the cloudflared add-on and an Access application:

  ```yaml
  additional_hosts:
    - hostname: "dash.<domain>"
      service: "http://<docker-vm-ip>:8080"
    - hostname: "flights.<domain>"
      service: "http://192.168.1.60:8080"
  ```

  Then Zero Trust → Access → Applications → add `flights.<domain>` with the
  same *Emails = you* policy. **This step is mandatory** for the tunnel path:
  the report server has no login of its own and `POST /run` would otherwise be
  callable by anyone. Never port-forward 8080.

## 8. Watch the watcher

- **Uptime Kuma** (roadmap M1): an HTTP monitor on
  `http://192.168.1.60:8080/status.json`, expect 200, keyword `"healthy": true`.
  That catches both "the service is down" and "the collector has stopped
  collecting" from outside the guest.
- The app also pushes its own health warning through ntfy when nothing has
  succeeded for 36 h, every request fails, or the page layout changes
  (`health:` in `config.yaml`).
- `systemctl status flighttrack-serve flighttrack-update.timer` and
  `journalctl -u flighttrack-serve -n 100` are the local view.

## 9. Option B: the Docker VM instead

If you skip the LXC, on the Docker VM:

```bash
git clone https://github.com/alaurin10/flight-tracking /opt/flight-tracking && cd /opt/flight-tracking
cp .env.example .env && nano .env config.yaml        # NTFY_TOPIC, TZ, your trips
docker compose up -d && docker compose logs -f
```

Port 8080 collides with the Home-Hub dashboard on that VM; change the
`ports:` line to `"8081:8080"` and use 8081 everywhere above. The same
poll-and-deploy timer pattern from `apps/dashboard/systemd/` applies.

## 10. Snapshot, then forget it

`pct snapshot 110 baseline` after the first successful run, per your snapshot
policy. From here on the only recurring work is reading pushes and, once a
season, revising `target_price` values from what `advise` and the grids have
shown you.

---

## Checklist

- [ ] LAN IP chosen and reserved on the router
- [ ] LXC created (`pct-create.sh`) and in the vzdump backup job
- [ ] `install.sh` run; timezone set
- [ ] `/etc/flighttrack.env` has a long random `NTFY_TOPIC`; ntfy app subscribed
- [ ] `config.yaml`: real trip dates, targets, watches
- [ ] `doctor` passes; price units confirmed against Google Flights
- [ ] `doctor --calendar` checked; `calendar.enabled` set accordingly
- [ ] `calibrate` run; `fetch.max_queries_per_run` set below the measured limit
- [ ] first run triggered; report loads at `http://<ip>:8080/`
- [ ] Pi-hole record `flights.home.arpa`; Tailscale and/or `flights.<domain>` + Access
- [ ] Uptime Kuma monitor on `/status.json`
- [ ] Snapshot taken
