# Deploying Vouch

One process, one port, plain files. There is no build step:
`python server.py` starts the API and serves the UI from `ui/` on the
same port.

What runs: uvicorn on `127.0.0.1:8077` by default (`VOUCH_HOST`,
`VOUCH_PORT`). What to persist: `.data/` (cases and accounts — one
shared SQLite file), `data/` (reports), `.cache/` (registry mirror).
`backups/` holds the output of `python backup.py`.

## Install

Python 3.13, then from the repo root:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

(On Linux/macOS the middle line is `source .venv/bin/activate`.)

Run every `test_*.py` file, then `python eval.py` — the same commands
`.github/workflows/ci.yml` runs. Then boot and check health:

```powershell
python server.py
```

```powershell
python -c "import json,urllib.request; print(json.load(urllib.request.urlopen('http://127.0.0.1:8077/api/health', timeout=30))['ok'])"
```

That prints `True`. With the server still running, `python smoke.py`
exercises every demo scenario end to end over HTTP. The working app
itself is at `http://127.0.0.1:8077/app`.

## Environment

| Variable | Default | Purpose |
|---|---|---|
| VOUCH_HOST | 127.0.0.1 | Interface the server binds. Keep localhost behind a proxy. |
| VOUCH_PORT | 8077 | Port the server binds. |
| VOUCH_ADMIN_PASSWORD | (unset, 8+ chars when set) | Creates the `admin` account at first boot. Without it, the first registered account becomes admin. |
| VOUCH_DATA_DIR | repo root | Parent of `.data/` (cases, accounts) and `data/` (reports). |
| VOUCH_AUTH_DB | .data/vouch.sqlite | Account database. Shared with cases unless overridden. |
| VOUCH_AUDIT_LOG | .data/audit.log | Append-only security log. |
| VOUCH_CACHE_DIR | .cache | Greenbook mirror directory. |
| VOUCH_BACKUP_DIR | backups/ | Where backup runs land. |
| VOUCH_AUTO_SYNC | (off) | Set to `1` for a daily mirror refresh inside the server. |
| VOUCH_ORIGINS | (same-origin only) | Extra CORS origins, comma-separated. The bundled UI needs none. |
| MODEL_API_KEY | (unset) | Meta model key for agent reasoning, explanations, and photo reading. Without it, rules, Windows OCR, and typed entries take over. |
| META_API_KEY | (unset) | Alias for MODEL_API_KEY. |
| META_BASE | https://api.meta.ai/v1 | Model API base URL. |
| VOUCH_MODEL | muse-spark-1.3 | Model for agent reasoning and explanations. |
| META_VISION_MODEL | muse-spark-1.3 | Model for photo reading. |
| VOUCH_MFR_URL | (inactive) | Manufacturer serial-check endpoint. `demo` exercises the wiring. |
| VOUCH_MFR_KEY | (unset) | Bearer key sent to the serial endpoint. |
| VOUCH_MAS_URL | (inactive) | Scratch-PIN verification endpoint. |
| VOUCH_MAS_KEY | (unset) | Bearer key sent to the PIN endpoint. |
| VOUCH_SMS_SECRET | (required for SMS) | Shared secret for POST /api/sms/inbound; wrong or missing means 403. |
| VOUCH_WA_VERIFY | (unset) | Webhook verify token shared with Meta. |
| VOUCH_WA_APP_SECRET | (unset) | Meta app secret; unsigned POSTs get 403. |
| VOUCH_WA_TOKEN | (unset) | API token for replies. `demo` rehearses without sending. |
| VOUCH_WA_PHONE_ID | (unset) | Sender number id replies come from. |

Put deployment values in an env file the service loads — never in the repo.

## TLS

Terminate TLS in a reverse proxy and keep uvicorn on localhost (the
default). Only the proxy faces the internet. Caddy is the shortest
correct path — this Caddyfile gets and renews certificates itself:

```
vouch.example.com {
    reverse_proxy 127.0.0.1:8077
}
```

Any TLS-terminating proxy forwarding to `127.0.0.1:8077` works
(nginx plus certbot is the common alternative).

Phone cameras need a secure context: HTTPS or localhost. A LAN install
over plain `http://` cannot open the camera — typing values, uploading
photos, and everything else still works.

## Run as a service

One server per data directory — SQLite does not share. The default
single uvicorn worker is correct; do not add workers.

Linux (systemd), `/etc/systemd/system/vouch.service`:

```ini
[Unit]
Description=Vouch
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/vouch
EnvironmentFile=/etc/vouch.env
ExecStart=/opt/vouch/.venv/bin/python server.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```sh
sudo systemctl enable --now vouch
journalctl -u vouch -f
```

`/etc/vouch.env` holds `KEY=value` lines (`VOUCH_ADMIN_PASSWORD=...`,
`VOUCH_AUTO_SYNC=1`).

Windows, with NSSM:

```powershell
nssm install Vouch "C:\vouch\.venv\Scripts\python.exe" "C:\vouch\server.py"
nssm set Vouch AppDirectory "C:\vouch"
nssm set Vouch AppEnvironmentExtra VOUCH_ADMIN_PASSWORD=... VOUCH_AUTO_SYNC=1
nssm start Vouch
```

Without NSSM, a basic task in Task Scheduler that runs `python server.py`
at startup with `C:\vouch` as its Start-in directory does the job.

## Verify and operate

- Health: the one-liner under Install prints `True`. CI boots the
  server and asserts the same response.
- Smoke: `python smoke.py` with the server running.
- Backups: schedule `python backup.py` (cron or Task Scheduler).
  Restore with `python backup.py --restore backups/<stamp>` or the
  admin endpoints, then restart the server.
- Logs: uvicorn stdout (the journal or NSSM) for requests;
  `.data/audit.log` for security events, readable at `GET /api/audit`.
- Rollback: check out the previous commit, restore a backup if data
  moved forward, restart. Every restore keeps a `pre-restore-*`
  snapshot beside the runs.

## Launch checklist

- [ ] every `test_*.py` plus `python eval.py` green
- [ ] `VOUCH_ADMIN_PASSWORD` set (8+ chars); first sign-in works
- [ ] TLS live; health prints `True` from outside
- [ ] backups scheduled; one restore rehearsed
- [ ] `VOUCH_ORIGINS` untouched (the bundled UI is same-origin)
- [ ] `VOUCH_AUTO_SYNC=1` if the mirror should refresh itself
