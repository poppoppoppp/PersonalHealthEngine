# Frequent Collection — from once a day to every 2 hours

Date: 2026-08-29
Owner: deployment
Status: READY-TO-DEPLOY (server steps below need SSH)

## Why

The app previously showed data that was always a day old: the pipeline ran
once per day at 10:30 China time. Everything needed for frequent collection
already existed on the VPS — the L1 collector with its file-based keyring, the
incremental checkpoint-based L2→L5 chain, the pipeline/L7 consistency locks,
and the post-pipeline L7 refresh. The only thing that made data stale was the
schedule in `phe-daily.timer`.

## What changed

`deployment/systemd/phe-daily.timer` only:

- `OnCalendar=*-*-* 10:30:00` → `OnCalendar=*-*-* 0/2:30:00 Asia/Shanghai`
  (every 2 hours at :30; 10:30 remains one of the runs)
- `phe-daily.service` Description updated; unit names unchanged, so nothing
  else in the deployment references new names.

## Why every 2 hours is safe

- The sealed L1 collector issues a handful of read-only API calls per run and
  deduplicates through its state file. Xiaomi's own app syncs far more often.
- The L2→L5 chain is checkpoint-based: a run with no new upstream data is a
  no-op that finishes in seconds.
- `daily.lock` prevents overlapping instances, and `TimeoutStartSec=90min` is
  strictly below the 2-hour interval, so overlap cannot happen anyway.
- `pipeline.lock` serializes each cycle against L7 reads, unchanged.
- L7 refresh after each cycle is gated by the Recompute Threshold: when the
  evidence bundle is unchanged no model is called. Expect roughly 0–8 extra
  DeepSeek calls per day (deepseek-v4-flash, compact bundle — negligible).

## Server rollout (needs SSH, run as root or with sudo)

```bash
cd /opt/phe
git pull origin main
systemctl daemon-reload
systemctl restart phe-daily.timer
systemctl list-timers phe-daily.timer     # shows the next ~8 runs
```

Optionally trigger one cycle immediately instead of waiting for the next
slot (the app shows fresher data right away):

```bash
systemctl start phe-daily.service
journalctl -u phe-daily.service -f         # watch it finish
```

Verify afterwards: `journalctl -u phe-daily.service --since today` should show
completed cycles, and the app's 健康依据 page should show data no older than
~2 hours (once the band has synced to Xiaomi Cloud).

## Rollback

One line + reload:

```bash
# in /opt/phe/deployment/systemd/phe-daily.timer
OnCalendar=*-*-* 10:30:00 Asia/Shanghai
```
```bash
systemctl daemon-reload && systemctl restart phe-daily.timer
```

## Watch-items after enabling

1. Judgment churn on partial-day data: mid-day steps are always "below
   baseline" by construction. The UI Change Threshold keeps wording stable
   (only "更新于" moves), but watch whether the Today judgment flips between
   B/C states unhelpfully during the day. If it does, the next lever is to
   make L5 analytics ignore intra-day partial features — a sealed-layer
   change, deliberately not done now.
2. Capture growth: L1 writes one capture file per run (~8/day). Check
   `/srv/phe` disk usage after a few weeks; add a capture-rotation cron if
   needed.
3. Windows PC: the old 10:30 scheduled task (`run_daily_collector.ps1`) is
   redundant now and can be disabled on the PC.

## Explicitly out of scope

- True real-time (band → phone BLE → Xiaomi Cloud sync latency is a hard
  ceiling that no collector change can beat).
- Webhook/push from Xiaomi (no such public API; polling is the only option).
