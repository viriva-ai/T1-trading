# Setup guide: a static-IP Linux VM for T1

This guide sets up T1 on an Oracle Cloud Always Free VM in Mumbai or Hyderabad running Ubuntu. Any Linux server with a fixed public IP works the same way.
It was last run end-to-end on **AWS Lightsail Mumbai, Ubuntu 22.04, 4 GB / 2 vCPU** (16 Nov 2026); see [1b](#1b-alternative-aws-lightsail-mumbai) for that route.
Times are IST. The guide ends in **paper mode**. Going live is a separate, deliberate step: see [LIVE_TRADING.md](LIVE_TRADING.md).

## Why a static IP

From **1 April 2026**, SEBI's retail-algo framework requires every API order to come from a **static IP registered with your broker**. Zerodha asks you to enter that IP in the Kite developer console.

Market-data calls, such as quotes and historical candles, work from any IP. Order calls from an unregistered IP are rejected. Home broadband and most cloud VMs change their IP unless you reserve it.

## 1. Create the VM (Oracle Cloud Always Free)

1. Sign up at <https://www.oracle.com/cloud/free/>. Choose **India South (Hyderabad)** or **India West (Mumbai)** as your **home region**. Always Free resources exist only in the home region, and you can't change it later.
2. In the console, go to **Compute → Instances → Create instance**:
   - **Image:** Canonical **Ubuntu 24.04**.
   - **Shape:** **VM.Standard.A1.Flex** (Ampere, Always Free eligible) with **2–4 OCPU and 12–24 GB RAM**. The model training needs about 2 GB. The 1 GB E2.1.Micro shape is too small.
     - "Out of capacity" errors are common. Retry later, try another availability domain, or upgrade the account to Pay-As-You-Go. Always Free resources stay free after the upgrade, but set a budget alert.
   - **Networking:** create a new VCN with a **public subnet**, and keep "Assign a public IPv4 address" on for now.
   - **SSH keys:** upload your public key (`~/.ssh/id_ed25519.pub`), or let Oracle generate a pair and download it.
   - **Boot volume:** 50 GB is enough. The data is about 3 GB.
3. Create the instance and wait until it shows **Running**.

## 1b. Alternative: AWS Lightsail (Mumbai)

1. Lightsail → **Create instance** → region **Mumbai (ap-south-1)** → **Linux/Unix → OS only → Ubuntu 22.04 LTS** (not an app blueprint).
2. Plan: **4 GB RAM / 2 vCPU / 80 GB** (about $24/month). The 512 MB and 1 GB plans are too small for training.
3. After it starts: **Networking → Attach static IP**. The public IP changes on stop/start until you do this.
4. **Networking → Firewall:** delete the default **HTTP 80** rule and keep only **SSH 22**. T1 serves nothing.
5. Lightsail instances are dual-stack, and Kite/NSE resolve to IPv6 first, so outbound API calls would leave from the
   IPv6 address, not the static IPv4 you register with Zerodha. Step 3 below makes the VM prefer IPv4.
6. Add swap so training can't be OOM-killed: `sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile && echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab`.
7. Set `model.n_jobs: 2` in `config/config.yaml` (2 vCPU).

Then continue at step 3.

## 2. Reserve a static public IP

An ephemeral IP survives stop and start, but is lost if the instance is ever recreated. A reserved IP stays with you.

1. Go to **Networking → IP Management → Reserved public IPs → Reserve public IP address**, and give it a name such as `t1-static`.
2. Go to **Compute → Instances → your instance → Attached VNICs → (primary VNIC) → IPv4 addresses**. On the primary private IP, choose **Edit**:
   - First set the public IP to **No public IP** and save.
   - Then edit again, choose **Reserved public IP**, select `t1-static`, and save.
3. Note the reserved IP, for example `140.238.x.y`. Free tenancies usually get **one** reserved IP. Check under **Governance → Limits, Quotas and Usage** (`reserved-public-ip-count`).
4. The default security list allows inbound SSH (22) only. That is all T1 needs, so don't open any other port.

## 3. SSH in and prepare the OS

```bash
ssh -i ~/.ssh/your_key ubuntu@<RESERVED_IP>

sudo timedatectl set-timezone Asia/Kolkata          # all schedules are IST
timedatectl                                         # confirm "Time zone: Asia/Kolkata (IST, +0530)"
sudo apt update && sudo apt -y upgrade
sudo apt -y install python3 python3-venv python3-pip git build-essential cmake libgomp1 unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades     # automatic security updates
# dual-stack VMs (Lightsail, some Oracle VCNs): make outbound calls prefer IPv4, or Kite sees your IPv6 address
echo 'precedence ::ffff:0:0/96  100' | sudo tee -a /etc/gai.conf
curl -s https://ifconfig.me ; echo                  # must print your RESERVED/STATIC IPv4 (this is the IP Kite will see)
```

Optional hardening:
- Turn off SSH password and root login: put `PasswordAuthentication no` and `PermitRootLogin no` in `/etc/ssh/sshd_config.d/10-hardening.conf`, then `sudo sshd -t && sudo systemctl reload ssh`. Test a fresh login before closing your session.
- Firewall: `sudo ufw allow 22/tcp && sudo ufw --force enable`.
- `sudo apt -y install fail2ban`. Give it room for scripted logins so you don't lock yourself out: in `/etc/fail2ban/jail.local` set `[sshd]` `enabled = true`, `maxretry = 10`, `findtime = 10m`, `bantime = 15m`, then `sudo systemctl restart fail2ban`. If you do get banned, use the provider's browser console and run `sudo fail2ban-client unban --all`.
- Long jobs (bootstrap, `--bootstrap-hn`) should run in `tmux` so a dropped SSH session doesn't kill them; check their logs with short SSH calls instead of holding a session open.

## 4. Clone and install

The pinned versions (numpy 2.5, pandas 3.1) need **Python 3.12 or newer**. Ubuntu 24.04 ships 3.12, so `python3 -m venv .venv` works there.
On **Ubuntu 22.04** (Python 3.10), get Python 3.13 with uv instead:
`curl -LsSf https://astral.sh/uv/install.sh | sh && ~/.local/bin/uv python install 3.13`, then replace the `python3 -m venv .venv` line below with `~/.local/bin/uv venv --python 3.13 --seed .venv`.

```bash
cd ~
git clone https://github.com/viriva-ai/T1-trading.git
cd T1-trading
python3 -m venv .venv                               # needs Python >= 3.12 (see above)
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt           # lightgbm, numpy, pandas and scipy ship aarch64 wheels
.venv/bin/python -m pytest -q                       # expect: 22 passed
cp config/config.example.yaml config/config.yaml    # git-ignored; edit capital / paths if you like
```

## 5. Secrets: environment file outside the repo

```bash
mkdir -p ~/.config/t1 && chmod 700 ~/.config/t1
cp .env.example ~/.config/t1/env && chmod 600 ~/.config/t1/env
nano ~/.config/t1/env        # set KITE_API_KEY and KITE_API_SECRET from developers.kite.trade -> My apps
```

- `deploy/run.sh`, which cron uses, loads this file automatically.
- Optional: add `T1_LOG_DIR=/home/ubuntu/T1-trading/logs` to keep cron logs inside the repo folder (git-ignored). The default is `~/t1-logs`.
- For an interactive shell, run `set -a; . ~/.config/t1/env; set +a`.
- Never put the key or secret in `config.yaml`, in the repo, or in shell history.

## 6. Kite developer console

Go to <https://developers.kite.trade> → **My apps** → your app (Kite Connect, with historical data if you want Kite candles):

1. **Redirect URL:** `http://127.0.0.1:5000/callback`. Kite allows `http://127.0.0.1` for this.
2. **Static IP:** enter the **reserved IP** from step 2 in the app's IP whitelist field. It is needed for orders and is harmless for paper.
3. **Postback URL:** leave it empty.
4. Keep the subscription active. Market data and orders stop when it expires.
5. If the API secret has ever been shared anywhere, click **Re-generate API secret** and update `~/.config/t1/env`.

## 7. Daily Kite login

Zerodha expires every access token at about **06:00 IST**, so you need to log in once each trading day before 09:10. There are three ways to do it.

**A. Paste the redirect (simplest)**
```bash
cd ~/T1-trading && set -a; . ~/.config/t1/env; set +a
.venv/bin/python scripts/kite_login.py
```
1. Open the printed login URL on your phone or laptop and log in with your Zerodha ID, password and 2FA.
2. The browser lands on `http://127.0.0.1:5000/callback?request_token=...&status=success`. The page won't load, which is expected.
3. Copy the **whole address** and paste it at the prompt. The token is saved to `~/.config/t1/kite_token.json` with chmod 600.

**B. SSH tunnel (no copy-paste)**
```bash
# on your laptop
ssh -L 5000:127.0.0.1:5000 ubuntu@<RESERVED_IP>
# in that SSH session on the VM
cd ~/T1-trading && set -a; . ~/.config/t1/env; set +a; .venv/bin/python scripts/kite_login.py --listen
```
Open the login URL in the laptop browser. The redirect travels through the tunnel to the VM and is captured automatically.

**C. From a shortcut:** `scripts/kite_login.py --url "<redirect url>"`.

Check the login at any time with `scripts/kite_login.py --status`. It exits 0 when the token is valid and 3 with `TOKEN_EXPIRED` plus the login URL when it isn't, so the 08:50 cron log shows `FAILED (exit 3)` on days you still need to log in. Paper mode does not need Kite at all, because NSE public files drive it. Kite is only used for the optional close cross-check, live marks, and (later) orders.

## 8. First data download and model

```bash
cd ~/T1-trading
.venv/bin/python scripts/bootstrap_data.py         # NSE bhavcopy + MTO 2010→today, Yahoo/FRED/liquid NAV (1-3 h, resumable)
.venv/bin/python scripts/train_model.py --bootstrap-hn   # one-time HN history: 240 small fits (~26 min on Lightsail 2 vCPU / 4 GB, peak ~1.3 GB RAM)
.venv/bin/python scripts/train_model.py            # current M4 block (5 fits, ~1 min)
.venv/bin/python scripts/weekly_signal.py --no-refresh
```

- Run long jobs inside `tmux` so a dropped SSH session doesn't kill them (`sudo apt install tmux; tmux new -s t1`).
- If NSE starts returning 403, wait a while and re-run. Downloads are paced and resume where they stopped.

## 9. Paper mode

`config/config.yaml` ships with `mode: paper`, `live_trading_enabled: false` and `paper_inception: "2026-11-20"`.

The 20 Nov 2026 paper rules are already set:
- **real Zerodha fees:** `fees.card: zerodha_2026`
- **liquid-fund interest on idle cash:** `cash.earn_liquid_fund_interest: true`
- **current M4 model**

```bash
.venv/bin/python scripts/daily_run.py              # refresh -> score -> replay paper book -> next-session orders
cat ~/t1-state/summary.json; column -s, -t < ~/t1-state/positions.csv
.venv/bin/python scripts/execute_orders.py --phase open    # PAPER: prints and records the orders it would send
.venv/bin/python scripts/execute_orders.py --phase stops
```

The paper book is replayed from `paper_inception` every run on frozen weekly scores, so it is idempotent: run it as often as you like.

## 10. Cron (IST)

```bash
crontab -e      # paste deploy/crontab.example (fix the path if your home is not /home/ubuntu)
```

| When (IST) | Job |
|---|---|
| Mon–Fri 08:50 | `kite_login.py --status`: reminds you to log in before 09:10 |
| Mon–Fri 09:16 | `execute_orders.py --phase open`: pending exits, then entries whose open is above their stop |
| Mon–Fri 09:25 | `execute_orders.py --phase stops`: SL-M stop for every holding |
| Mon–Fri 19:45 | `daily_run.py --kite-check`: data refresh, scoring, paper book, tomorrow's orders |
| Mon–Fri 21:30 | `daily_run.py`: retry for late NSE files (idempotent) |
| Fri 20:30 | `weekly_signal.py --no-refresh`: the week's ranking |
| Sun 10:00 | backup of `~/t1-state` and `~/t1-models` |

- The order jobs run in **paper** unless every live switch is on.
- Logs go to `$T1_LOG_DIR/<job>_YYYYMMDD.log` (default `~/t1-logs`). A non-zero exit appends `<job> FAILED (exit N)`.
- Before the paper book's first rebalance (`paper_inception`), the 09:16/09:25 order jobs log `nothing to do` and exit 0, and `daily_run` prints the latest signal instead of a book.
- Jobs share a non-blocking lock: if one is still running (for example a manual `--bootstrap-hn`), a cron job that starts meanwhile exits with `another T1 job is running` and the 21:30 retry catches up.
- Install without an editor: `grep -v '^#' deploy/crontab.example | grep . | crontab - && crontab -l`.
- Optional: set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in the env file to get the daily summary on your phone.

## 11. Backtests

```bash
.venv/bin/python scripts/backtest.py --start 2024-10-23 --end 2026-10-22     # holdout window, walk-forward M4
.venv/bin/python scripts/backtest.py --start 2015-01-01 --end 2024-10-22     # development window (trains ~40 blocks first)
```
Results are written to `~/t1-state/backtests/YYYYMMDD_YYYYMMDD/` (stats.json, equity.csv, fills.csv, trades.csv). Read [STRATEGY.md](STRATEGY.md#reproducing-the-numbers) for why these won't exactly equal the sealed figures.

## 12. Monitoring and logs

- `~/t1-logs/daily_run_YYYYMMDD.log`: the last line is the equity summary. Watch for `FAILED`, `PROVISIONAL` and `REFIT DUE`.
- `~/t1-state/summary.json`, `positions.csv`, `pending_orders.csv`, `ledger.csv` and `equity.csv` are the paper book.
- `~/t1-state/paper_orders_YYYYMMDD.csv` holds the orders the executor would have sent.
- `scripts/live_mark.py` gives an intraday read-only mark on Kite LTPs.
- Disk: `du -sh ~/t1-*`. Memory: `free -h`.

More operating detail, including the refit calendar, holidays and troubleshooting, is in [OPERATIONS.md](OPERATIONS.md).

## 13. Security checklist

- [ ] `~/.config/t1/` is mode 700, and `env` and `kite_token.json` are mode 600.
- [ ] The repo has no `config/config.yaml`, `.env` or token files committed (`git status` is clean).
- [ ] SSH uses keys only, only port 22 is open, and unattended-upgrades is on.
- [ ] The API secret has been regenerated if it was ever shared.
- [ ] `mode: paper` and `live_trading_enabled: false` stay set until you have read LIVE_TRADING.md.
- [ ] `touch ~/t1-state/KILL` stops all live orders immediately.
