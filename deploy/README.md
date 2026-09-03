# Deploying C-ECM

Three ways to run this, all built on the same `docker-compose.yml`:

- **Standalone** — one command, anywhere Docker runs (a laptop, a bare
  Linux server, an on-prem box).
- **AWS** — `aws/deploy.sh` provisions one EC2 instance and runs the same
  stack on it.
- **Azure** — `azure/deploy.sh` provisions one Azure VM and runs the same
  stack on it.

All three assume you **don't** need real FileNet content-write support. If
you do, see [Windows-native (FileNet) deployment](#windows-native-filenet-deployment)
below instead — that's a genuinely different architecture, not a flag on
this one.

## Standalone

```bash
cd deploy
cp .env.production.example .env    # then edit .env — at minimum set
                                    # FD_DB_PASSWORD, FD_APP_PASSWORD, and
                                    # FD_OAUTH_REDIRECT_BASE
docker compose up -d --build
```

That's it. First start creates the Postgres database and every table/index
C-ECM needs automatically (see `backend/app/db.py`) — there's no separate
migration step to run. Open `http://localhost/` (or whatever `HTTP_PORT`
you set).

- `docker compose logs -f backend` — tail the backend's logs.
- `docker compose down` — stop everything, keep all data (named volumes).
- `docker compose down -v` — stop everything **and delete all data**.

## AWS

```bash
cd deploy
cp .env.production.example .env && $EDITOR .env
./aws/deploy.sh my-ec2-keypair us-east-1 t3.medium
```

Provisions a security group (22/80/443 open — narrow this to your own
IP/VPN before real use), launches an Ubuntu 24.04 instance, installs
Docker on it, syncs this repo up, and starts the stack. Re-running the
script reuses the same instance instead of creating a new one.

## Azure

```bash
cd deploy
cp .env.production.example .env && $EDITOR .env
./azure/deploy.sh ~/.ssh/id_rsa.pub eastus Standard_B2s
```

Same shape as the AWS script: creates a resource group + VM, opens
80/443, installs Docker, syncs the repo, starts the stack.

## Before this is really "production"

The scripts above get you a working, reachable deployment fast — they
deliberately don't do everything a hardened production setup needs:

- **TLS.** Nothing here terminates HTTPS. Put a real domain + certificate
  in front — either a cloud load balancer (an AWS ALB or Azure Application
  Gateway/Front Door, both handle cert provisioning/renewal for you), or
  add a Certbot/Let's Encrypt sidecar to the nginx service yourself.
  `FD_OAUTH_REDIRECT_BASE` must match whatever public HTTPS URL you land
  on — set it before registering any OAuth app or SAML IdP against this
  deployment; changing it later means re-registering every callback URL.
- **Database backups.** `postgres_data` is a plain Docker volume — set up
  `pg_dump` on a schedule, or point `FD_DB_HOST` at a managed instance
  instead (AWS RDS / Azure Database for PostgreSQL) for automatic
  backups/HA, which `FD_DB_ENGINE=postgres` already supports without any
  code change — just point the connection settings at it.
- **Secrets.** `deploy/.env` holds plaintext credentials on whatever host
  runs it — fine for a quick deployment, but move to your cloud's secrets
  manager (AWS Secrets Manager / Azure Key Vault) before this holds real
  customer data.
- **Scaling beyond one box.** This is a single-instance deployment by
  design (matching what was asked for). Running more than one backend
  replica needs a shared session store first — see the load-testing
  report earlier in this project for why (`_app_sessions` is in-process,
  per-worker memory today).

## Choosing a database

`FD_DB_ENGINE` supports `sqlite` (default, zero external dependency —
fine for a quick eval, not for real concurrent production use), `postgres`
(what these scripts use, and the recommended choice), and `oracle` (fully
supported by the same code path if your organization standardizes on it —
swap the `postgres` service in `docker-compose.yml` for a reachable Oracle
instance and set `FD_DB_ENGINE=oracle` / `FD_DB_URL` accordingly).

## Windows-native (FileNet) deployment

Only needed if you actually require FileNet content-**write** support —
reads and every other one of the 54 storage providers work identically
either way. See `windows/install-native-backend.ps1` for the full script;
the shape is:

1. `docker compose -f windows/docker-compose.windows-hybrid.yml up -d --build`
   — starts Postgres + nginx in containers, same as the standalone stack.
2. Install WebSphere Application Server + FileNet's CEClient (Jace.jar)
   yourself first — licensed IBM software with its own installer, not
   something a script here can automate. Point `FD_WAS_JAVA_HOME` /
   `FD_JACE_JAR` / `FD_WAS_RUNTIMES` / `FD_WAS_PROFILE_PROPS` in
   `deploy\.env` at wherever you installed them (see
   `backend/app/config.py` for every setting and its default).
3. `windows\install-native-backend.ps1` — sets up a venv, installs
   dependencies, and registers the backend as a native Windows Service
   (via [NSSM](https://nssm.cc/)) so it survives reboots. The backend runs
   directly on the host (not containerized) specifically so its
   subprocess call to the local WebSphere Java runtime works; nginx
   proxies to it at `host.docker.internal:8020`.

For AWS/Azure with this path: adapt `aws/deploy.sh` / `azure/deploy.sh`'s
AMI/image lookup to a Windows Server image, RDP or SSH in, and follow the
three steps above on it directly — the provisioning scripts here don't
cover that automatically.
