# Where to run it

The tracker needs three things: to be awake once a day, a home (residential) IP
address, and somewhere you can open the report from your phone. That rules some
options in and some out.

## The options

| Where | Always on? | IP | Verdict |
|---|---|---|---|
| **Mini PC / NAS / Raspberry Pi 4+ at home** | yes, ~5–10 W | residential | **Recommended.** `docker compose up -d` and forget it. |
| **Your Mac** (launchd) | only if you leave it on or schedule a wake | residential | Fine for a Mac mini on a desk. A laptop that sleeps in a bag misses days; launchd runs the missed job at next wake, so history has gaps rather than holes. |
| **Cloud VPS** ($4–6/mo) | yes | datacenter | Google Flights is far more likely to serve captchas to datacenter ranges. Works with the paid SerpApi source as primary; not recommended for the free path. |
| **GitHub Actions cron** | yes, free | datacenter, shared | Same problem, worse. Good for the deal feeds only. |
| **A phone** | no | – | No. |

The residential-IP point is the important one. Everything this system does to
stay unblocked (pacing, jitter, cooldowns, a browser TLS fingerprint) assumes it
looks like one household checking fares. A cloud IP does not.

## Recommended: mini PC with Docker

Any small x86 or ARM box running Linux with Docker. The container is one
process: the daily job on a schedule plus the report server.

```bash
git clone https://github.com/alaurin10/flight-tracking.git
cd flight-tracking
cp .env.example .env          # set NTFY_TOPIC (long, random) and TZ
# edit config.yaml: your trips, patterns, watches
docker compose up -d
docker compose logs -f        # first run happens at 03:15 local; trigger one now:
curl -X POST http://localhost:8080/run
```

Then open **http://\<box\>:8080/** from any device on your home network.

What you get:
- the daily job at 03:15 (+ up to 45 minutes of jitter) — `--at` in the Dockerfile CMD if you want another time;
- the report at `/`, regenerated after every run;
- `/status.json` for a phone widget, Home Assistant, or a curl in a script;
- `POST /run` to run now (a bookmark on your phone, or a Shortcut);
- `restart: unless-stopped`, so a reboot brings it back;
- a container healthcheck, so `docker ps` shows *unhealthy* if the server dies.

### Reading it from your phone away from home

Do not port-forward 8080 to the internet; the server has no authentication.
Install [Tailscale](https://tailscale.com) on the box and on your phone and open
`http://<box-tailscale-name>:8080/`. That is a private network; nothing is exposed.
`tailscale serve 8080` even gives it HTTPS and a nice hostname.

### Without Docker

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[full]'
sudo cp deploy/flighttrack.service deploy/flighttrack.timer /etc/systemd/system/
sudo systemctl edit flighttrack.service     # add Environment=NTFY_TOPIC=...
sudo systemctl enable --now flighttrack.timer
```

The timer runs `flighttrack run` daily; serve the `out/` directory with whatever
web server you already have, or run `flighttrack serve` as its own service
instead of the timer.

## Your Mac

`deploy/com.flighttrack.daily.plist` is a launchd agent. Edit the paths and the
topic, then:

```bash
cp deploy/com.flighttrack.daily.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.flighttrack.daily.plist
launchctl kickstart -k gui/$(id -u)/com.flighttrack.daily     # run once now
```

Keep the Mac awake at 03:15 (`sudo pmset repeat wakeorpoweron MTWRFSU 03:10:00`)
or accept that a sleeping laptop runs the job when it next wakes. Open
`out/index.html` directly, or run `flighttrack serve` in a terminal when you want
it on your phone over the LAN.

## What to check after the first day

```bash
flighttrack status      # runs, request outcomes, health
flighttrack health      # exit 1 if the collector is unwell (also pushed to your phone)
```

If `status` shows a cooldown or layout failures, `flighttrack doctor` says which
part of the data path broke. See `docs/data-sources.md` for what each failure
means.
