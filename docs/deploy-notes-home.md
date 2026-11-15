# Deployment notes: the home setup, start to finish

Every step to get Flight Watch running on the mini PC at home and published at
`https://flights.andrewlaurin.com`, in order. The general reference is
[`DEPLOYMENT.md`](DEPLOYMENT.md); this file is the exact path for this machine.

**The shape of it.** The mini PC runs Proxmox. A Debian VM on it (the "Docker VM")
already runs Snow Watch and the Home Hub dashboard; Flight Watch joins them as two more
containers from `/opt/flight-tracking`. The Home Assistant VM runs the Cloudflared
app, whose tunnel already publishes `ha.`, `dash.` and `snow.`; it gets one more
hostname. No router port is opened: the tunnel is outbound only. Requests to Google
leave from the home address, which is what keeps them from being treated as a bot.

| Where | What |
|---|---|
| Docker VM (Debian 12) | `api` (port **8091**) and `worker`, from `/opt/flight-tracking`; the deploy timer |
| Same VM, other stacks | Home Hub dashboard on 8080, Snow Watch on 8090 |
| Home Assistant VM | The Cloudflared app (the tunnel) |
| Cloudflare | DNS for `andrewlaurin.com` and the tunnel edge (HTTPS) |
| GitHub | `main` of `alaurin10/flight-tracking`, which the deploy timer pulls every 5 minutes |

Budget about an hour, most of it waiting for the image to build and for the first prices.

Contents:

1. [Allocate the mini PC's hardware](#1-allocate-the-mini-pcs-hardware) (or [1b. a VM of its own](#1b-alternative-a-vm-of-its-own))
2. [Check the VM's software](#2-check-the-vms-software)
3. [Get the code](#3-get-the-code)
4. [Settings (`.env`)](#4-settings-env)
5. [Write your trips file](#5-write-your-trips-file)
6. [Build and start](#6-build-and-start)
7. [Live checks from the home address](#7-live-checks-from-the-home-address)
8. [Load your trips](#8-load-your-trips)
9. [Publish it through the Cloudflare tunnel](#9-publish-it-through-the-cloudflare-tunnel)
10. [Automatic updates from GitHub](#10-automatic-updates-from-github)
11. [Check everything from outside](#11-check-everything-from-outside)
12. [Backups off the machine](#12-backups-off-the-machine)
13. [Day to day, and changing settings later](#13-day-to-day-and-changing-settings-later)
14. [Troubleshooting](#14-troubleshooting)
15. [Moving over from checkpoint A](#15-moving-over-from-checkpoint-a) (only if you ran it)

---

## 1. Allocate the mini PC's hardware

The two VMs as they were set up (Proxmox → each VM → **Hardware**):

| VM | Memory | Cores (type) | Disk |
|---|---|---|---|
| 100 Home Assistant OS | 8 GiB | 4 (`kvm64`) | 64 GB |
| 102 Docker host (Home Hub, Snow Watch, now Flight Watch) | 6 GiB | 2 (default type) | 24 GB |

That is 14 GiB promised out of what the mini PC has, with most of it on the VM that
needs it least. Home Assistant OS with ordinary add-ons (Cloudflared, a few
integrations, no camera recording) uses 2–3 GiB. The Docker host carries three app
stacks, builds their images on every update, and from M6 also builds a frontend.

**1.1 Look before you change anything.**

- The mini PC's totals: Datacenter → *your node* → **Summary**: *CPU(s)* (cores) and
  *RAM usage* (the total after the slash).
- Home Assistant's real use, read **inside Home Assistant**, not in Proxmox. Proxmox's
  memory graph for a VM counts the guest's file cache as used, so a Home Assistant VM
  usually shows close to its full allocation (7 of 8 GiB is typical) whatever it
  needs. Instead:
  - Settings → System → **Hardware**: memory in use.
  - Settings → **Add-ons** → each add-on's Info tab: its own memory, to find a heavy one
    (Frigate, Whisper or Piper voice, InfluxDB with Grafana, Music Assistant, Node-RED;
    ESPHome spikes while compiling).
  - Most exact, with the *Terminal & SSH* add-on: `free -m`; real use is *total*
    minus *available*. Check at a busy moment, or a few times over a day.
- The Docker host's real use: in VM 102, `free -h`, `df -h /` and
  `docker stats --no-stream`.

**1.2 The recommended split.** Memory is the one resource that must not be promised
twice: leave about 2 GiB for Proxmox itself and split the rest. CPU cores can be
shared, so each VM can have all of them; Proxmox shares them as needed.

For a mini PC with **16 GiB and 4 cores** (the common N100-class box):

| VM | Memory | Cores | CPU type | Disk |
|---|---|---|---|---|
| 100 Home Assistant OS | 8 → **4 GiB** | 4 → **2** | `kvm64` → **host** | 64 GB, leave it |
| 102 Docker host | 6 → **10 GiB** | 2 → **4** | → **host** | 24 → **40 GB** |
| Proxmox itself | ~2 GiB left over | | | |

- If Home Assistant's real use (step 1.1) is 3–4.5 GiB, give it 6 GiB and the Docker
  host 8 GiB instead. Above that, find the heavy add-on first; to keep it, leave Home
  Assistant at 8 GiB and grow the Docker host only by what the node has spare.
- After shrinking Home Assistant, watch Settings → System → Repairs and the add-on
  logs for a day. An add-on killed for lack of memory shows there; raise it again
  (another Shutdown and Start).
- With **32 GiB**: Home Assistant 6 GiB, Docker host 16 GiB, and room for another VM
  later.
- With **8 cores**: Home Assistant 2, Docker host 6 or 8.
- Why `host` for the CPU type: `kvm64` hides the processor's newer instructions, which
  makes Python packages such as NumPy (Snow Watch) refuse to load or run slower, and
  makes image builds slower. Both VMs only ever run on this one machine, so nothing
  is lost.
- Why 40 GB: three stacks' images, a new image beside the old one during every
  update, Snow Watch's tile cache, and 14 nightly backups of each database. 24 GB
  gets tight.

**1.3 Make the changes.** Each change only takes effect after a full **Shutdown** and
**Start** from Proxmox (a reboot from inside the VM is not enough). Do Home Assistant
first so the memory it gives up is free:

1. **VM 100 (Home Assistant).** VM 100 → **Shutdown** (Home Assistant and every
   tunnel hostname are offline for a couple of minutes). Then **Hardware**:
   - **Memory** → double-click → *Memory (MiB)* `4096`. If the *Advanced* box shows
     *Ballooning Device* ticked with a *Minimum memory*, set the minimum to the same
     value or untick it.
   - **Processors** → double-click → *Cores* `2`, *Type* `host`.
   - **Start**. Check Home Assistant opens and Settings → System → Repairs is clean.
2. **VM 102 (Docker host).** VM 102 → **Shutdown** (the dashboard and Snow Watch are
   offline for a minute). Then **Hardware**:
   - **Memory** → `10240` (or what step 1.2 gave you).
   - **Processors** → *Cores* `4`, *Type* `host`.
   - **Hard Disk (scsi0)** → **Disk Action → Resize** → *Size Increment (GiB)* `16`.
   - Optionally **Options → QEMU Guest Agent** → Enabled (then
     `sudo apt-get install -y qemu-guest-agent` inside the VM), so Proxmox shows its IP
     and shuts it down cleanly.
   - **Start**.
3. **Grow the file system** on VM 102. It was made from a Debian cloud image (it has a
   *CloudInit Drive*), and cloud-init grows the root partition to fill the disk at boot.
   Check:
   ```bash
   df -h /
   ```
   If it still shows the old size:
   ```bash
   lsblk
   ```
   ```bash
   sudo apt-get install -y cloud-guest-utils && sudo growpart /dev/sda 1 && sudo resize2fs /dev/sda1
   ```
   (In a cloud image the root file system is `sda1`, the last partition; `lsblk`
   confirms the name.)
4. In VM 102: `free -h` shows the new memory, `nproc` the cores, and
   `docker compose ps` in each app's folder shows its containers back up
   (`restart: unless-stopped` starts them).
5. Datacenter → *your node* → **Summary**: RAM usage now leaves 2 GiB or more free.

Leave **Machine** (`q35` on VM 100, `i440fx` on VM 102) and **BIOS** as they are:
changing them can stop a VM from booting and gains nothing here.

**1.4 Give the Docker host a fixed address.** The tunnel points at the VM's IP, so it must not
change. If it does not already have one, add a DHCP reservation for the VM's MAC
address in the router (the MAC is in Proxmox → the VM → Hardware → **Network Device**).
Note the IP: it is `<docker-vm-ip>` below.

## 1b. Alternative: a VM of its own

Use this instead of step 1 only if you would rather keep Flight Watch apart from the
other stacks. It needs memory taken from Home Assistant all the same (step 1.2). Everything after step 2 is the same.

1. Download the Debian 12 netinst ISO to Proxmox: *your node* → **local** storage →
   **ISO Images** → **Download from URL** (the current `debian-12.*-amd64-netinst.iso`
   link from debian.org).
2. **Create VM**: name `flightwatch`; OS: that ISO; System: defaults (q35, Qemu agent
   ticked); Disks: **20 GiB**; CPU: **2 cores**, type `host`; Memory: **2048 MiB**;
   Network: the same bridge as the other VMs (usually `vmbr0`).
3. Start it, open **Console**, install Debian with only **SSH server** and **standard
   system utilities** selected. Create your user.
4. In the VM: `sudo apt-get install -y qemu-guest-agent && sudo systemctl enable --now qemu-guest-agent`.
5. Options → **Start at boot**: Yes.
6. Give it a fixed address (step 1.4), then install Docker (step 2.2).

The port 8091 is still fine on its own VM; the tunnel then points at this VM's IP.

## 2. Check the VM's software

**2.1 What is already there.** On the Docker VM:

```bash
docker --version && docker compose version && git --version
```

You need **Docker Engine 25 or newer** and **Docker Compose 2.24 or newer**
(`docker-compose.yml` uses `env_file: required: false` and health check
`start_interval`, which older versions reject), and git. Also check your user can run
Docker without sudo:

```bash
docker ps
```

**2.2 If Docker is missing or too old** (Debian 12, Docker's own packages):

```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl git
```
```bash
sudo install -m 0755 -d /etc/apt/keyrings && sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc && sudo chmod a+r /etc/apt/keyrings/docker.asc
```
```bash
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
```
```bash
sudo apt-get update && sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```
```bash
sudo usermod -aG docker "$USER"
```

Log out and back in (for the group), then `docker ps` works without sudo.

**2.3 Your user id.** The containers run as uid 1000 and write to `./data`:

```bash
id -u
```

If it prints `1000`, nothing to do. Otherwise you will run one `chown` in step 3.

**2.4 The clock.** The worker's daily sweep and nightly backups run on local time:

```bash
timedatectl
```

`System clock synchronized: yes` is what matters; the containers get the timezone from
`TZ` in `.env`.

## 3. Get the code

The repository is public, so no deploy key is needed. Clone it to `/opt`, owned by your
user (the deploy timer runs as you and must be able to `git fetch` there):

```bash
sudo mkdir -p /opt/flight-tracking && sudo chown "$USER": /opt/flight-tracking
```
```bash
git clone https://github.com/alaurin10/flight-tracking.git /opt/flight-tracking
```
```bash
cd /opt/flight-tracking && mkdir -p data
```

If `id -u` was not 1000:

```bash
sudo chown -R 1000:1000 /opt/flight-tracking/data
```

Rule for this checkout from now on: **never edit tracked files in it**
(`docker-compose.yml`, `config/`, anything under `backend/`). The deploy timer only
fast-forwards, and a local edit stops every future update. Changes go through GitHub
(step 13). Your own files (`.env`, `data/`) are ignored by git and are safe.

## 4. Settings (`.env`)

```bash
cd /opt/flight-tracking && cp .env.example .env && chmod 600 .env
```

Make a password (or use your password manager):

```bash
openssl rand -base64 24
```

Edit `.env` (`nano .env`) and set these lines; leave the rest as they are:

```ini
TZ=America/Los_Angeles
HOME_AIRPORT=SEA
OWNER_PASSWORD=<the password: 12 characters or more>
PUBLIC_URL=https://flights.andrewlaurin.com
TRUSTED_PROXY=1
```

What each does:

| Setting | Why |
|---|---|
| `TZ` | The calendar sweep (04:10) and the nightly backup (03:30) run on this clock |
| `HOME_AIRPORT` | The default origin, and what `doctor` tests from |
| `OWNER_PASSWORD` | Unlocks editing. Under 12 characters, sign-in stays off (the API logs an error) |
| `PUBLIC_URL` | Owner requests must come from this address (the `Origin` check) |
| `TRUSTED_PROXY=1` | Visitors arrive through Cloudflare; this makes the rate limits read each visitor's real address from Cloudflare's header instead of counting everyone as the tunnel |

`ENV=prod` is set by `docker-compose.yml` itself (HTTPS-only cookie, HSTS, no API docs).
Leave the `VAPID_*` and `CONTACT_EMAIL` lines empty until push alerts arrive (M8).

## 5. Write your trips file

What is tracked lives in the database. Since M7 the easy way is the site's **Manage**
page: once the site is up, open Manage, sign in with `OWNER_PASSWORD` and add your trips
there; skip this step and step 8. A YAML file is still the way to load many at once.
**Keep this file outside the checkout**: the repository is public, and real trip dates
must never be committed.

```bash
cp /opt/flight-tracking/config/seed.example.yaml ~/flight-trips.yaml && chmod 644 ~/flight-trips.yaml
```
```bash
nano ~/flight-trips.yaml
```

Replace the invented examples with your own. The shape:

```yaml
home: SEA

routes:                     # each destination's label and fallback target, in CENTS
  - {dest: HND, label: Tokyo, target_price: 90000}

trips:                      # fixed dates: "when should I book?"
  - name: japan             # becomes the URL slug: /trip/japan
    title: Japan in spring
    dest: HND
    depart: 2027-04-10
    return: 2027-04-24      # leave out for one way
    target_price: 85000     # $850.00

patterns:                   # flexible dates: "which weekend is cheapest?"
  - name: ski-weekends
    title: Ski weekends
    depart_dow: [THU, FRI]
    nights: 3
    routes: [SLC]           # several destinations make one search
    window: {start: 2027-01-01, end: 2027-03-31}   # leave out for the rolling horizon
    priority: 1             # 1 = refreshed daily, 2 = every 3 days, 3 = weekly

deals:
  watches:                  # announced deals in the feeds
    - name: Europe from Seattle
      origins: [Seattle, SEA, West Coast, US cities]
      destinations: [europe]
      max_price: 60000
```

Limits: 20 active trips, 10 active searches, 400 date pairs in all (the import says so
if you go over). Prices are in cents: `85000` is $850.

## 6. Build and start

```bash
cd /opt/flight-tracking && GIT_COMMIT=$(git rev-parse --short HEAD) docker compose up -d --build
```

The first build takes a few minutes (it downloads Python packages). Then:

```bash
docker compose ps
```

Within a minute both `flightwatch-api-1` and `flightwatch-worker-1` should say
`(healthy)`. The worker creates `data/flights.db` as it starts. Quick checks:

```bash
curl -s localhost:8091/api/livez
```
```bash
curl -s localhost:8091/api/meta
```

`livez` answers `{"ok":true}`; `meta` shows the commit and `"owner_login_enabled":true`
(if it says `false`, the password in `.env` is shorter than 12 characters). Nothing is
tracked yet, so the worker has nothing to fetch.

## 7. Live checks from the home address

These make real requests to Google, once, to confirm the requests and the parser work
from home and to measure how many requests this address may make. Stop the worker
first so it does not compete:

```bash
docker compose stop worker
```

**7.1 One results page:**

```bash
docker compose run --rm worker flighttrack doctor
```

It prints `[ok]` lines and a list of itineraries with prices. Compare two or three
prices with google.com/travel/flights for the same route and dates (it prints them):
they should match within a few dollars. Wait a couple of minutes before the next one.

**7.2 One calendar call:**

```bash
docker compose run --rm worker flighttrack doctor --calendar
```

It prints a list of dates with prices. If they look sensible, the calendar sweep can be
turned on (step 13, `calendar.enabled: true`). Wait a few minutes.

**7.3 Measure the limit** (20 requests, 6 seconds apart; about two minutes):

```bash
docker compose run --rm worker flighttrack calibrate --count 20 --interval 6 --yes
```

Write down what it reports: how many succeeded before the first block, if any.

- All 20 succeeded: the shipped budget (`collect.daily_request_budget: 50` a day) is
  conservative; it can be raised later, to at most 80 (step 13).
- It was blocked after N requests: Google will cool off within an hour or so. Keep the
  budget at 50 or lower it toward N/2 a day if N is small.

Never run `calibrate` repeatedly; it is a one-off measurement.

Start the worker again:

```bash
docker compose start worker
```

## 8. Load your trips

```bash
cd /opt/flight-tracking && docker compose run --rm -v "$HOME/flight-trips.yaml:/tmp/trips.yaml:ro" worker flighttrack import-config /tmp/trips.yaml
```

It prints what it created and the size of the date grid (`… active total (N fixed trips,
M searches)`). An error names the line that is wrong; fix the file and run it again
(importing again updates by name and never deletes).

The worker schedules its first batch 1–5 minutes after it started, then one batch of 5
searches every 25–45 minutes. Within about ten minutes:

```bash
curl -s localhost:8091/api/health
```

says `"status":"ok"`, and `http://<docker-vm-ip>:8091/` from a computer at home shows
the page with your trips. If `health` still says `no_data` after 15 minutes, see
[Troubleshooting](#14-troubleshooting).

## 9. Publish it through the Cloudflare tunnel

**9.1 Add the hostname to the tunnel.** In Home Assistant → **Settings → Add-ons**
(called **Apps** in newer versions) → **Cloudflared** → **Configuration**. Under
`additional_hosts`, next to the existing `snow.` entry, add:

```yaml
- hostname: "flights.andrewlaurin.com"
  service: "http://<docker-vm-ip>:8091"
```

**Save**, then **Restart** the add-on (Info tab). Its **Log** tab should show the
tunnel reconnecting with no errors. Home Assistant itself is reachable from outside
again within a few seconds.

**9.2 Cloudflare DNS.** With this setup (a tunnel the add-on manages from its
configuration, as for `snow.`), the add-on creates the DNS record itself. Check it in
the Cloudflare dashboard → `andrewlaurin.com` → **DNS → Records**: there should be a
`CNAME` named `flights` pointing to `<tunnel-id>.cfargotunnel.com`, **Proxied** (orange
cloud), the same as the `snow` record.

If it is not there after a minute:

- If the add-on uses a **tunnel token** (a tunnel managed in the Cloudflare dashboard;
  the add-on's configuration then has `tunnel_token` set), add the hostname there
  instead: Cloudflare **Zero Trust → Networks → Tunnels** → your tunnel → **Public
  Hostname → Add a public hostname**: subdomain `flights`, domain `andrewlaurin.com`,
  type `HTTP`, URL `<docker-vm-ip>:8091`. Save; Cloudflare creates the DNS record.
- Otherwise add it by hand: **DNS → Records → Add record**: type `CNAME`, name
  `flights`, target the same `….cfargotunnel.com` value the `snow` record has,
  Proxied on.

**9.3 Nothing else on Cloudflare.**

- **Do not** create a Cloudflare Access application for `flights.`: the site is public
  and read-only, and has its own owner sign-in. (If a wildcard Access application
  covers `*.andrewlaurin.com`, exclude `flights.` from it.)
- **Do not** add a "Cache Everything" cache rule for it: prices and sign-in must not be
  cached at the edge. Cloudflare's default (it does not cache JSON) is right.
- HTTPS is Cloudflare's edge certificate; the SSL/TLS mode does not matter for a tunnel.
  **SSL/TLS → Edge Certificates → Always Use HTTPS** on is fine.

**9.4 Apply `PUBLIC_URL`.** You set it in step 4, so nothing to do. If you change it
later: `docker compose up -d`.

## 10. Automatic updates from GitHub

There is no push from GitHub to the house (that would need an open port). Instead a
systemd timer on the Docker VM runs `deploy/deploy.sh` every 5 minutes. It fetches
`main`; if nothing changed it exits; if only docs changed it fast-forwards; if app files
changed (`backend frontend config docker docker-compose.yml`) it fast-forwards, rebuilds
the image and restarts both containers, then prunes old images. A failed build leaves
the running containers as they were. GitHub's own CI only checks pull requests; nothing
is deployed from GitHub.

So the release process is: **merge a pull request into `main`, and within about 10
minutes it is running at home.**

**10.1 Install the timer:**

```bash
cd /opt/flight-tracking
```
```bash
sudo cp deploy/flightwatch-deploy.service deploy/flightwatch-deploy.timer /etc/systemd/system/
```
```bash
sudo sed -i "s/REPLACE_WITH_YOUR_USER/$USER/" /etc/systemd/system/flightwatch-deploy.service
```
```bash
sudo systemctl daemon-reload && sudo systemctl enable --now flightwatch-deploy.timer
```

(The service runs `/opt/flight-tracking/deploy/deploy.sh` as your user, who is in the
`docker` group. If you cloned somewhere else, fix `ExecStart` in
`/etc/systemd/system/flightwatch-deploy.service`.)

**10.2 Check it:**

```bash
systemctl list-timers flightwatch-deploy.timer
```
```bash
sudo systemctl start flightwatch-deploy.service && journalctl -u flightwatch-deploy -n 20 --no-pager
```

`list-timers` shows the next run within 5 minutes. A manual run on an up-to-date
checkout prints nothing and exits successfully. After a real deploy the journal shows
`deploying <old> -> <new>` and `deployed <new>`.

**10.3 Watch a deploy happen** (optional, the first time): merge any pull request, then
`journalctl -u flightwatch-deploy -f`. `curl -s localhost:8091/api/meta` shows the new
commit afterwards.

## 11. Check everything from outside

On a phone with Wi-Fi **off**:

1. `https://flights.andrewlaurin.com/` shows the page with your trips.
2. `https://flights.andrewlaurin.com/api/health` shows `"status":"ok"`.

From a laptop (any network), the owner sign-in: open the **Manage** page and sign in; it
lists what is tracked. Or check it with `curl`:

```bash
curl -s -c /tmp/fw -H 'Origin: https://flights.andrewlaurin.com' -H 'Content-Type: application/json' -d '{"password":"<your password>"}' https://flights.andrewlaurin.com/api/auth/login
```
```bash
curl -s -b /tmp/fw https://flights.andrewlaurin.com/api/auth/me
```

Both should show `"is_owner":true`. Then `rm /tmp/fw`.

Finally, in the router's admin page: still **no port-forward rules**.

## 12. Backups off the machine

The worker writes `data/backups/flights-YYYYMMDD.db` and `tracked-YYYYMMDD.yaml` every
night at 03:30 and keeps 14 of each. That protects against a bad update, not against
losing the VM. Price history cannot be collected again, so copy it somewhere else too.
Either of these is enough:

- **Proxmox backups**: Datacenter → **Backup** → the job that already backs up the
  Docker VM covers `/opt/flight-tracking/data`. If there is none: **Add** → node, your
  backup storage (a NAS or USB disk, not the same SSD), schedule daily, Selection: the
  Docker VM, Mode: Snapshot, Retention: keep 7 daily.
- **A copy to the NAS** from the VM, nightly at 05:00 (`crontab -e`, one line, with your
  NAS path mounted or reachable over SSH):
  ```
  0 5 * * * rsync -a /opt/flight-tracking/data/backups/ nas:/backups/flightwatch/
  ```

Restoring is in [`DEPLOYMENT.md`](DEPLOYMENT.md#backups-and-restore).

## 13. Day to day, and changing settings later

| You want to… | Do |
|---|---|
| See whether it is working | the page's Collector section, `https://flights.andrewlaurin.com/api/status`, or `docker compose exec worker flighttrack status` |
| See alerts | until push alerts (M8), they are on the page and appended to `data/out/digest.md` (`cat /opt/flight-tracking/data/out/digest.md`) |
| Add or change trips | edit `~/flight-trips.yaml`, run the step 8 command again; set `status: paused` or `archived` to stop one (history is kept) |
| Check now | `docker compose exec worker flighttrack advise` for verdicts; a re-import queues nothing, but saving through the API does |
| Change a tunable (request budget, `calendar.enabled`, alert thresholds, feeds) | edit `config/flighttrack.yaml` **on GitHub** (open the file → pencil → commit to a new branch → pull request → merge). The timer deploys it. Never edit it in `/opt/flight-tracking` |
| Change the password | edit `.env`, `docker compose up -d`, then `docker compose run --rm worker flighttrack owner logout-all` |
| Read logs | `docker compose logs -f worker`, `docker compose logs -f api` |
| Stop it | `docker compose down` (data stays in `data/`) |
| Pause updates | `sudo systemctl stop flightwatch-deploy.timer` (start it again to resume) |

Future milestones arrive through the timer like any update. M7 (the Manage page) needed
nothing but signing in. M8 needs a step from you when it lands, and its pull request will
say so (push alerts: `docker compose run --rm worker flighttrack push keys`, paste two
lines into `.env`, `docker compose up -d`).

## 14. Troubleshooting

| Symptom | What to check |
|---|---|
| `docker compose up` says `env_file` or `start_interval` is invalid | Docker or Compose too old: step 2.2 |
| `permission denied` writing `/data` or `flights.db` | `sudo chown -R 1000:1000 /opt/flight-tracking/data` |
| `import-config` cannot read `/tmp/trips.yaml` | `chmod 644 ~/flight-trips.yaml` (the container runs as uid 1000) |
| `/api/health` stays `no_data` | `docker compose logs worker`: is anything tracked (`import-config` printed a grid)? Is there a cooldown (`docker compose exec worker flighttrack status`)? |
| `in cooldown until …` | Google blocked a request; the worker waits (1 h, then 4, 12, 24) and resumes by itself. Repeated blocks: lower the budget (step 13) |
| `https://flights.…` gives a Cloudflare 1033 / 502 | The tunnel cannot reach the VM: is `curl http://<docker-vm-ip>:8091/api/livez` working from the Home Assistant VM's network, is the IP in `additional_hosts` right, did the add-on restart cleanly? |
| `https://flights.…` does not resolve | The DNS record is missing: step 9.2 |
| A Cloudflare login page appears | An Access application covers the host: step 9.3 |
| Owner sign-in returns 403 | The request's `Origin` is not `PUBLIC_URL`: check `.env` and `docker compose up -d` |
| Sign-in says "not set up" | `OWNER_PASSWORD` empty or under 12 characters |
| Updates stopped arriving | `journalctl -u flightwatch-deploy -n 50`. `fatal: Not possible to fast-forward` means a local edit: `cd /opt/flight-tracking && git status`, then `git stash` or `git checkout -- <file>` |
| Memory pressure on the VM | `docker stats --no-stream`; step 1.2 |
| A VM will not start after the hardware change | The node does not have the memory: lower one VM's memory (step 1.2) |

---

## 15. Moving over from checkpoint A

Only if you ran the old single container from checkpoint A (the static page on port
8081). Its database carries forward; nothing is collected twice. Do this **instead of
step 3**, then continue at step 4:

1. Stop it, and keep your edited `config.yaml` and a copy of the data:
   ```bash
   cd /opt/flight-tracking && docker compose down
   ```
   ```bash
   cp config.yaml ~/flight-trips.yaml && sudo cp -a data ~/flight-data-before-m5
   ```
2. Give the data to uid 1000 (the old image ran as root) and drop the local edits so
   the deploy timer can fast-forward:
   ```bash
   sudo chown -R 1000:1000 data
   ```
   ```bash
   git checkout -- . && git pull
   ```
3. Continue from step 4. In step 8, `~/flight-trips.yaml` is the old `config.yaml`: it
   loads the old trips, patterns and deal watches with their names, so the history
   behind every date pair stays attached.
4. The page moves from port 8081 to 8091; remove any bookmark to the old one.
