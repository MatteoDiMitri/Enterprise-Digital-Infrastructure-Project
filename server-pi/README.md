# NEXUS — Pi Server

> A full-stack observability playground — PHP, MariaDB, Prometheus, and a Python metrics API, all containerized and ready to run on a Raspberry Pi.

NEXUS simulates a live e-commerce service and wires it up for full observability: scrape metrics with Prometheus, inspect them through a custom Python dashboard API, and watch a MariaDB-backed PHP frontend serve traffic — all in a single `docker compose up`. Built to run lean on ARM64, so your Pi is a first-class citizen.

---

## ⚡ TL;DR — Pi setup & auto-deploy on push

**First time on the Pi:**

```bash
# 1. Install Docker
curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $USER
sudo apt install -y docker-compose-plugin

# 2. Clone & configure
git clone <your-repo-url> ~/Enterprise-Digital-Infrastructure-Project && cd ~/Enterprise-Digital-Infrastructure-Project/server-pi
# Copy the provided template and edit secure values
cp .env.example .env
# On Windows PowerShell:
# copy .env.example .env
# Edit `.env` and set strong passwords (MYSQL_ROOT_PASSWORD, MYSQL_USER, MYSQL_PASSWORD).

# 3. Launch
docker compose up -d --build

```

**What happens after a `git push`? (alternative workflows)**

By default, pushing to GitHub does not update your Raspberry Pi automatically. Common options:

* **Manual update:** SSH into the Pi and run `git pull` in the repository and then `docker compose up -d --build`, or run the included `deploy.sh` script.
* **CI-driven image deploy (recommended for constrained Pis):** Build multi-arch images in CI and push them to a registry (GHCR/Docker Hub). On the Pi pull the new images and restart the stack with `docker compose pull && docker compose up -d`.
* **SSH-based auto-deploy (see below):** Configure a GitHub Action or webhook that SSHes into the Pi and runs `deploy.sh`. This is simple and works for small setups but requires exposing SSH access for the deploy user.

**Auto-deploy on every `git push**` — add these secrets to your GitHub repo (`PI_HOST`, `PI_USER`, `PI_SSH_KEY`) then create `.github/workflows/deploy-to-pi.yml` from the template below. Every push to `main` will SSH into the Pi and run:

```bash
cd ~/Enterprise-Digital-Infrastructure-Project && git reset --hard origin/main && cd server-pi && ./deploy.sh

```

**Survive reboots:**

```bash
sudo systemctl enable nexus-docker.service   # see systemd section below

```

---

## Stack at a glance

| Layer | Technology | Notes |
| --- | --- | --- |
| Frontend |   | Built from `web/Dockerfile` |
| Database |  | Auto-seeded from `init.sql` |
| Metrics |  | Config in `prometheus/prometheus.yml` |
| API backend |  | Serves `/api/dashboard_metrics`, `/api/scenario`, `/healthz` |
| Runtime |  | Named volumes for persistence |
| Target HW |  | ARM64, validated |

---

## Ports

| Service | URL |
| --- | --- |
| Web (Apache/PHP) | `http://<pi-ip>:8080` |
| Dashboard backend API | `http://<pi-ip>:8881` |
| Prometheus UI | `http://<pi-ip>:9091` |

---

## Quick start on a Raspberry Pi (recommended)

This section walks you through a minimal, reliable setup for Raspberry Pi 4 (4GB+) or better. Use a 64-bit OS (Raspberry Pi OS 64-bit or Ubuntu Server). The stack has been validated against ARM64 images; older 32-bit Pi OS may require extra changes.

### Prerequisites

* Raspberry Pi 4 (4GB+) or similar; 64-bit OS strongly recommended.
* Up-to-date system packages.
* Docker + Docker Compose (plugin) installed.
* `git` access.

### Install Docker & Compose (one-liners)

Run on the Pi:

```bash
# Install Docker (official convenience script)
curl -fsSL https://get.docker.com -o get-docker.sh && sh get-docker.sh
# Enable current user to use Docker without sudo
sudo usermod -aG docker $USER
# Install the Docker Compose plugin if not present
sudo apt update && sudo apt install -y docker-compose-plugin
# Log out and back in (or reboot) to apply group change

```

If you prefer the distro packages or `docker.io`, use those instead.

### Clone & configure

```bash
cd ~
git clone <your-repo-url> Enterprise-Digital-Infrastructure-Project
cd Enterprise-Digital-Infrastructure-Project/server-pi

```

Use `.env.example` as a template. Copy it to `.env` next to `docker-compose.yml` and edit the values with strong, unique passwords.

```bash
cp .env.example .env
# or on Windows PowerShell:
# copy .env.example .env
# Then edit `.env` and set secure values, for example:
# MYSQL_ROOT_PASSWORD=change_this_root_pw
# MYSQL_USER=nexus
# MYSQL_PASSWORD=change_this_pw

```

Use strong passwords and never commit a real `.env` file to a public repository; `.env.example` is safe to commit and documents required keys.

### Start the stack

```bash
# Build & start (builds local images like web when needed)
docker compose up -d --build
# Check running containers
docker compose ps

```

Visit `http://<pi-ip>:8080` to see the web site and `http://<pi-ip>:9091` for Prometheus. The dashboard UI pages are in `web/html/` and the dashboard API is available at `http://<pi-ip>:8881`.

---

## Raspberry Pi specific notes & tips

* **Architecture:** some upstream images may not publish ARM32/ARM64 variants. The provided `web/Dockerfile` is based on `php:8.4-apache` which generally has multi-arch manifests; `mariadb` and `python` official images often support ARM too. If you hit architecture errors, build/push multi-arch images from a builder or use ARM-friendly tags.
* **Building on-device can be slow and memory-heavy.** Consider two alternatives:
1. Build multi-arch images with GitHub Actions and push to a registry (GHCR/Docker Hub), then `docker pull` on the Pi.
2. Use `docker buildx` on a more powerful machine to produce `linux/arm64` images and push them.


* The included `init.sql` contains a MariaDB dump (note: it was exported for `aarch64`), so the DB schema and seed data will be applied automatically on first container start.
* If you see out-of-memory builds: add swap temporarily or build elsewhere and push images.

---

## Make the stack start at boot (systemd)

Create `/etc/systemd/system/nexus-docker.service` (example):

```ini
[Unit]
Description=NEXUS Docker Compose stack
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/pi/Enterprise-Digital-Infrastructure-Project/server-pi
ExecStart=/usr/bin/docker compose up -d --build
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target

```

Then enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable nexus-docker.service
sudo systemctl start nexus-docker.service

```

Adjust `WorkingDirectory` to where you cloned the repo.

---

## Redeploy on push (recommended workflows)

There are two common approaches to update the Pi after you push to GitHub:

1. **SSH-based deploy from GitHub Actions (recommended):** set up an SSH key pair for the Action and a small deploy script on the Pi. The example workflow below will SSH into the Pi and run a safe pull + restart.
2. **Build & publish multi-arch images from CI**, and have the Pi pull the updated images and run `docker compose up -d`.

### Minimal `deploy.sh` (place in repo and make executable)

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
cd ..
# ensure we are on the intended branch
git fetch origin
git reset --hard origin/main
cd server-pi
# pull new images if they exist (no-op if images built locally)
docker compose pull || true
# recreate containers with new images / code
docker compose up -d --build --remove-orphans
# optional cleanup
docker image prune -f || true

```

Make it executable: `chmod +x deploy.sh`.

### Example GitHub Actions workflow (push → SSH deploy)

Create `.github/workflows/deploy-to-pi.yml` in your project (example):

```yaml
name: Deploy to Raspberry Pi
on:
  push:
    branches: [ main ]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Deploy via SSH
        uses: appleboy/ssh-action@master
        with:
          host: ${{ secrets.PI_HOST }}
          username: ${{ secrets.PI_USER }}
          key: ${{ secrets.PI_SSH_KEY }}
          port: ${{ secrets.PI_SSH_PORT }}
          script: |
            cd /home/pi/Enterprise-Digital-Infrastructure-Project
            git fetch origin
            git reset --hard origin/main
            cd server-pi
            ./deploy.sh

```

Secrets to add in the repository settings: `PI_HOST`, `PI_USER` (e.g. `pi`), `PI_SSH_KEY` (private key), and `PI_SSH_PORT` (optional).

> **Notes:**
> * Ensure the SSH public key is added to `~/.ssh/authorized_keys` for the deploy user on the Pi.
> * Consider locking the Action to a known commit of `appleboy/ssh-action` instead of `master`.
> 
> 

---

## Multi-arch build (optional advanced)

If you want CI to build images for multiple architectures, use `docker/setup-qemu-action`, `docker/setup-buildx-action` and `docker/build-push-action` in CI, and push to a registry. Then your Pi can simply `docker pull` the updated images and `docker compose up -d`.

---

## Useful files

| File | Purpose |
| --- | --- |
| `docker-compose.yml` | Main stack definition |
| `web/Dockerfile` | PHP/Apache image build |
| `backend/dashboard-server.py` | Python dashboard backend (config via `NEXUS_PROM_URL`, `NEXUS_BIND_PORT`) |
| `prometheus/prometheus.yml` | Prometheus job configuration |
| `init.sql` | MariaDB schema + seed data |

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| MariaDB won't start | `docker compose logs db` — ensure `.env` has valid passwords |
| Architecture errors on build | Build for `linux/arm64` or push multi-arch images from CI |
| Permission denied to Docker socket | Ensure your user is in the `docker` group and re-login |

---

## Security

* Never commit `.env` or secrets to a public repository. Use GitHub Secrets for CI.
* Use firewall rules / network isolation for production deployments.