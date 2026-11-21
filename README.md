# KTS Host VPS Manager

A Discord.py 2.x prefix bot for provisioning and managing per-user Ubuntu 24.04 Docker containers. Prefix: `>`.

## Prerequisites

- Python 3.10 or later (Python 3.11+ recommended)
- A running Docker daemon on a Linux host, accessible to the bot process
- A Discord application/bot token
- Discord Developer Portal intents enabled: **Message Content Intent** and **Server Members Intent**

## Setup

1. Create and activate a virtual environment.

   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env`, then set `DISCORD_TOKEN`. On Linux, leave `DOCKER_BASE_URL` unset for Docker's default socket. On Windows Docker Desktop, configure it only if your Docker SDK endpoint differs from the default environment.

3. Ensure the operating-system account running the bot can access Docker. On Linux this commonly means adding it to the `docker` group, then signing in again.

4. Run the bot:

   ```powershell
   python bot.py
   ```

## Deploy and update from GitHub

Create an empty GitHub repository, then publish this project from its folder on your Windows computer:

```powershell
git init -b main
git add .
git status
git commit -m "Initial bot source"
git remote add origin https://github.com/<your-user>/<your-repository>.git
git push -u origin main
```

Check `git status` before committing: `.env`, runtime JSON files in `data/`, and ZIP archives are ignored so tokens and VPS records are not uploaded.

On the Linux VPS that runs the bot, clone it once:

```bash
git clone https://github.com/<your-user>/<your-repository>.git
cd <your-repository>
```

Keep the VPS's `.env` and `data/` files local. To deploy later changes, push them from your computer with `git add`, `git commit`, and `git push`; on the VPS run `git pull --ff-only` in the cloned folder, then restart the bot using its normal service or run command. For a private repository, configure GitHub authentication on the VPS; do not put access tokens in the clone URL or commit them.

## Commands

| Command | Access | Purpose |
| --- | --- | --- |
| `>help` | Everyone | Show available bot commands and access levels. |
| `>specs <ram_mb> <cores>` | Server owner | Set the per-VPS RAM and CPU limits for new VPSes in this server (e.g. `>specs 127000 24`). |
| `>create @user` | Server owner / bot admin | Provision a VPS with the configured per-VPS limits; warns if the user already has one. |
| `>manage [@user] [vps_number]` | Owner for self; admin for any | Open that VPS's persistent management panel (defaults to VPS #1). |
| `>stats [@user] [vps_number]` | Owner for self; admin for any | Show the selected VPS's current live usage (defaults to VPS #1). |
| `>sshx [@user] [vps_number]` | Owner for self; admin for any | Start SSHX for the selected VPS and DM the private link (defaults to VPS #1). |
| `>admin add @user` | Server owner | Grant bot-admin access. |
| `>admin remove @user` | Server owner | Revoke bot-admin access. |
| `>admins` | Everyone | List configured bot admins. |

## Operational notes

- The server owner always has full permission and is never required in `admins.json`.
- `>specs 127000 24` sets new VPSes to 127,000 MB RAM and 24 CPU cores. Specs are saved per Discord server; existing containers keep their current limits and the `>create` result and `>manage` panel show each container's actual limits. Until configured, new VPSes default to 32,768 MB RAM and 4 cores.
- A user may own multiple VPS containers. The first uses `user_<Discord ID>` / `KTS-<Discord ID>`; additional VPSs use `-1`, `-2`, and so on. To select a VPS other than #1, include its number after the optional member, for example `>manage @member 2`. Users managing their own VPS can mention themselves to select a number, e.g. `>stats @self 2`.
- Existing single-VPS records are treated as VPS #1 and are converted to the multi-VPS format when the user data is next updated.
- New containers use a locally built Ubuntu image with systemd as PID 1, plus Git, `curl`, `nano`, and `ca-certificates`. The first creation builds this image and requires the Docker daemon to have network access to Ubuntu package repositories.
- To deploy from a Git repository, run `git clone <repository-url>` in your VPS, `cd` into the project directory, and use `git pull` there to fetch updates. Public repositories work directly over HTTPS. Private repositories require Git credentials configured inside the VPS; never paste access tokens into Discord or commit them to the repository.
- Bot panels and informational cards use Discord Components V2; discord.py 2.6.1 or later is required.
- New containers run in privileged mode with host networking and a writable cgroup mount to support nested Docker and Pterodactyl Wings. This removes normal Docker isolation: container owners can gain control of the Docker host. Use only on a dedicated, trusted Linux host; do not expose this setup to untrusted users.
- Managed containers use Docker's `unless-stopped` restart policy so a systemd reboot or unexpected PID 1 exit brings the VPS back up. Existing managed containers are updated to this policy when accessed by the bot; a deliberate Stop action remains stopped. The management panel shows Docker's exit code or OOM status when available.
- All managed containers on the same Docker daemon share the host network. Configure Wings allocations against the host's public IP and avoid port collisions. Multiple nested Docker daemons can also conflict over host networking and iptables; use a separate Linux Docker host for each Wings VPS when isolation or reliable networking is required. Docker Desktop/Windows hosts do not provide the required Linux host-network behavior.
- Existing containers do not receive new image packages automatically. Git can be installed into an existing VPS with `apt-get update && apt-get install -y git` if its package manager is healthy. New containers receive Git preinstalled. Recreating a VPS can lose files stored only inside that container, so back up its data before recreating it.
- Docker API operations are run in worker threads so Discord interactions remain responsive.
- The bot uses JSON files in `data/` and writes them atomically. Back them up before manual edits.
- Management views are persistent across restarts. Do not alter the `Owner ID` or `VPS ID` reference in a panel.
- SSHX links and log-derived session details are only ever sent by DM. Treat these links as credentials.
- Containers are labelled `kts.host.managed=true`, `kts.host.owner_id=<Discord ID>`, and `kts.host.vps_number=<number>` for operational auditing.

## Security

This bot grants an interactive root shell inside each container through SSHX. Because managed VPS containers are privileged and host-networked, root access inside any VPS is equivalent to host-level access and can affect other VPSes and the Docker host. Run this only on a dedicated, trusted Linux host, limit bot-admin membership, and keep Docker access restricted.
