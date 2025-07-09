# Windows Services with NSSM

This short guide shows how to register **Redis**, **QuestDB**, and the **Option Chain Worker** as Windows services using [NSSM](https://nssm.cc/). Once installed they will start automatically at boot and can be managed from the normal *Services* console.

> Adjust paths and ports to match your installation locations.

---
## Prerequisites

1. Download NSSM 2.24 or newer and unzip. In this guide
   ```text
   NSSM = C:\Tools\nssm\nssm-2.24\win64\nssm.exe
   ```
2. Run **PowerShell as Administrator** for all commands below.

We’ll use the following component paths:

| Component | Executable | Extra Args |
|-----------|------------|-----------|
| **Redis** | `"C:\Program Files\Redis\redis-server.exe"` | `--port 6380` |
| **QuestDB** | `"D:\questdb\bin\questdb.exe"` | *(leave blank for default port 8812)* |
| **OptionChainWorker** | `"C:\Users\rajiv\.pyenv\pyenv-win\versions\3.11.5\python.exe"` | `"D:\Backup\CrudeOil_NSSM_New\backup_2025-07-02_12-31-24\option_chain_worker.py"` |

---
## Generic NSSM command pattern

```powershell
& $NSSM install <ServiceName> <Executable> <Arguments>
sc config <ServiceName> start= auto        # Auto-start at boot
sc start  <ServiceName>                    # Launch now
```

To remove later:
```powershell
sc stop  <ServiceName>
& $NSSM remove <ServiceName> confirm
```

---
## 1. Redis service (port 6380)

```powershell
$NSSM = 'C:\Tools\nssm\nssm-2.24\win64\nssm.exe'
& $NSSM install Redis6380 "C:\Program Files\Redis\redis-server.exe" "--port 6380"
sc config Redis6380 start= auto
sc start  Redis6380
```

Verify:
```powershell
sc query Redis6380
```

---
## 2. QuestDB service

```powershell
& $NSSM install QuestDB "D:\questdb\bin\questdb.exe"
sc config QuestDB start= auto
sc start  QuestDB
```

QuestDB UI → http://localhost:9000 (default).

---
## 3. Option Chain Worker service (Python 3.11)

Below is a fully-working example that ensures:

* The correct Python interpreter is used (3.11.9 from *pyenv*).
* The working directory is set so that relative imports work.
* Stdout / stderr are written to log files under `D:\Backup\Logs`.
* All required environment variables (including a **PYTHONPATH** fallback to per-user site-packages) are passed **in a single call**.

> ⚠️  Run all commands in **Administrator PowerShell**.

```powershell
$NSSM      = 'C:\Tools\nssm\nssm-2.24\win64\nssm.exe'
$PY        = 'C:\Users\rajiv\.pyenv\pyenv-win\versions\3.11.9\python.exe'
$SCRIPT    = 'D:\Backup\CrudeOil_NSSM_New\backup_2025-07-02_12-31-24\option_chain_worker.py'
$WORKDIR   = Split-Path $SCRIPT
$LOGDIR    = 'D:\Backup\Logs'
$USER_PKGS = 'C:\Users\rajiv\AppData\Roaming\Python\Python311\site-packages'

# create (or update) service
& $NSSM install OptionChainWorker $PY $SCRIPT

# essential settings
& $NSSM set OptionChainWorker AppDirectory   $WORKDIR
& $NSSM set OptionChainWorker AppStdout      "$LOGDIR\option_chain_worker.out"
& $NSSM set OptionChainWorker AppStderr      "$LOGDIR\option_chain_worker.err"

# ALL env-vars in ONE call (otherwise the previous value is overwritten)
& $NSSM set OptionChainWorker AppEnvironmentExtra `
  "REDIS_URL=redis://localhost:6379/0;OC_REFRESH_SEC=8;PYTHONUNBUFFERED=1;PYTHONPATH=$USER_PKGS"

# auto-start and launch now
sc config OptionChainWorker start= auto
sc start  OptionChainWorker
```

If you want to keep the old definition around, install under another name, e.g. `OptionChainWorker2` (just replace the service name in the commands above).

---
### Removing / reinstalling

```powershell
sc stop  OptionChainWorker
& $NSSM remove OptionChainWorker confirm
```

---
### Troubleshooting tips

1. **ModuleNotFoundError (e.g. `redis`)**
   * Install the package system-wide with:
     ```powershell
     & $PY -m pip install <package>
     ```
   * Or keep the per-user install and ensure **PYTHONPATH** (shown above) points to your user site-packages.
2. **Service stuck in PAUSED / STOPPED**
   * Check the stderr log you configured (`option_chain_worker.err`).
   * Re-install or adjust `AppEnvironmentExtra` in **one** command.
3. **Access denied when using `sc`** – run PowerShell *as administrator*.


---
## Managing services

* **Start/Stop**: use *Services.msc* or `sc start|stop <ServiceName>`.
* **Status**: `sc query <ServiceName>`.
* **Logs**: check redirected log file (if configured) or use *Event Viewer → Windows Logs → Application*.

---
## 4. Web Dashboard service (Eventlet)

Run the Flask-SocketIO server directly with Eventlet:

```powershell
$NSSM = 'C:\Tools\nssm\nssm-2.24\win64\nssm.exe'

# create / update service
& $NSSM install AlgoTradeWeb "C:\Users\rajiv\.pyenv\pyenv-win\versions\3.11.5\python.exe" "mcx.py"

# set working directory so mcx.py can find templates / static
& $NSSM set   AlgoTradeWeb AppDirectory "D:\Backup\CrudeOil_NSSM_New\backup_2025-07-02_12-31-24"

# auto-start at boot
sc config AlgoTradeWeb start= auto
sc start  AlgoTradeWeb
```

Logs: use *I/O* tab to redirect stdout/stderr, or check Event Viewer.

---

### Quick checklist after reboot

1. `sc query Redis6380` → `RUNNING`
2. `sc query QuestDB` → `RUNNING`
3. `sc query OptionChainWorker` → `RUNNING`
4. Web app: browse to `http://localhost:5000/option_chain` (served by `AlgoTradeWeb`).

Everything should work without manually opening extra terminals.

---
## 5. Containerising the stack with Docker

### Dockerfile (backend + worker)
Create a `Dockerfile` at the project root:
```dockerfile
FROM python:3.11-slim

# system deps
RUN apt-get update && apt-get install -y build-essential && rm -rf /var/lib/apt/lists/*

# copy code
WORKDIR /app
COPY . /app

# install python deps
RUN pip install --no-cache-dir -r requirements.txt

# expose dashboard port
EXPOSE 5000

CMD ["python", "mcx.py"]
```
If you need a separate worker image, copy the same file but change the CMD to `option_chain_worker.py`.

### docker-compose.yml
```yaml
version: "3.9"
services:
  redis:
    image: redis:7-alpine
    ports: [ "6379:6379" ]
    restart: unless-stopped

  questdb:
    image: questdb/questdb:latest
    ports:
      - "9000:9000"     # Web UI
      - "8812:8812"     # Postgres wire protocol
    restart: unless-stopped

  dashboard:
    build: .
    ports: [ "5000:5000" ]
    environment:
      - REDIS_URL=redis://redis:6379/0
      - OC_REFRESH_SEC=8
    depends_on: [ redis, questdb ]
    restart: unless-stopped

  worker:
    build:
      context: .
      dockerfile: Dockerfile
    command: [ "python", "option_chain_worker.py" ]
    environment:
      - REDIS_URL=redis://redis:6379/0
      - OC_REFRESH_SEC=8
    depends_on: [ redis ]
    restart: unless-stopped
```
Run everything locally with:
```bash
docker compose up -d --build
```

---
## 6. CI/CD with GitHub Actions

Add `.github/workflows/docker.yml`:
```yaml
name: CI / CD

on:
  push:
    branches: [ main ]

jobs:
  build-and-push:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up QEMU
        uses: docker/setup-qemu-action@v3

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Login to Docker Hub
        uses: docker/login-action@v3
        with:
          username: ${{ secrets.DOCKER_USER }}
          password: ${{ secrets.DOCKER_PASS }}

      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          tags: ${{ secrets.DOCKER_USER }}/algo-trade-dashboard:latest
```

### Deployment options
* **Docker Hub ➜ any VPS** – pull the `latest` tag and `docker compose up -d`.
* **GitHub Actions + self-hosted runner on Windows** – reuse the NSSM scripts to run containers with `docker run --restart=always`.
* **Azure / AWS ECS / GCP Cloud Run** – the published image can be deployed directly.

---
### Git tips
1. Commit your `Dockerfile`, `docker-compose.yml`, and workflow YAML to the repo.
2. Store secrets (`DOCKER_USER`, `DOCKER_PASS`) in *GitHub → Settings → Secrets → Actions*.
3. Use feature branches; GitHub Actions will build PRs without pushing.

