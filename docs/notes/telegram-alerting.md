# Telegram threshold alerting

The pipeline sends a Telegram alert (via [telegram-relay](https://git.ardenone.com/jedarden/telegram-relay),
an internal HTTP relay deployed alongside this pipeline on ardenone-cluster)
whenever a listing matches **at least one alert rule**. There are two kinds:

**The global rule** (the original threshold) matches any listing that meets
**all three** of:

- `price_effective_monthly` strictly under a price ceiling — the same field
  the dashboard UI shows as "Price/mo*"
- `multi_thread_score` strictly above a benchmark floor (an unmatched CPU,
  with no score, never matches)
- `ram_gb` at or above a minimum

**Signature rules** (`TELEGRAM_ALERT_RULES`, added in 0.4.0) each name a set
of CPUs and match when all of the rule's set fields hold: `cpus` (normalized
name, case-insensitive exact), `min_ram_gb`, `ecc` (true/false, or omitted),
`min_nvme` (count of NVMe drives across all NVMe disk groups) and
`max_price_eur` (exclusive, on `price_effective_monthly`, so it includes the
€1.70 IPv4 line). No benchmark gate: the CPU is pinned already, and an
unmatched CPU (no score) can still match a signature rule.

Why signature rules exist: the pipeline's own `listing_history.parquet`
(60 s samples since 2026-08-20) shows that the servers worth buying clear at
a config-specific price, in minutes — an i5-13500 64 GB with two NVMe is
gone in a quarter of an hour at ≤ €70 and sits for a day at €95; a Ryzen 9
3900 128 GB ECC sells within ~3.5 h at €164–189. One global ceiling either
misses those or floods the channel. The production rule set and the sale
data behind each threshold are in the `configmap.yml` comments.

Reading the history for that analysis: 28% of listing ids that vanish are
Hetzner re-issuing the id (an identical `config_signature` twin appears within
60 minutes at the same price), so chain by signature before treating a
disappearance as a sale.

## Dedup model

A listing that stays under threshold across cycles alerts once, not every
cycle. State is a small JSON file (`alerted-listings.json`, key
`(listing_id, config_signature)` — the same identity `listing_history_store`
uses) fetched back from the live deployment before each cycle and rewritten
into the new generation, same pattern as `config_history.parquet` /
`listing_history.parquet`.

- Under threshold, not previously alerted → send, add to the persisted set.
- Under threshold, already alerted → skip, stays in the set.
- At/above threshold, or absent from this cycle's fetch → dropped from the
  set, so a later re-drop (price cut, or the listing reappearing) alerts
  again.
- Send failure → **not** added to the set, so it retries next cycle instead
  of being silently given up on.

This intentionally reuses `config_signature` (CPU + RAM + disk + datacenter)
rather than `listing_id` alone — Hetzner reuses `listing_id` across ticks
with different specs (EC-4, see `history_store.py`'s `build_config_signature`
docstring), so `listing_id` alone would misidentify a since-changed listing
as "already alerted." A listing that stops matching any one of the three
criteria (price rises, or a since-edited criteria set no longer fits it) is
dropped from the set exactly like a price rise always was — there's nothing
per-criterion here, it's one combined match/no-match per listing.

## Configuration

All in `pipeline-config` (`k8s/ardenone-cluster/hetzner-auction-dashboard/configmap.yml`):

| Var | Default | Purpose |
|---|---|---|
| `TELEGRAM_RELAY_URL` | in-cluster `telegram-relay` Service | Where to POST `{text}` |
| `TELEGRAM_ALERT_MAX_PRICE_EUR` | `55.00` | Price ceiling (exclusive) |
| `TELEGRAM_ALERT_MIN_MULTI_THREAD_SCORE` | `30000` | Benchmark floor (exclusive) |
| `TELEGRAM_ALERT_MIN_RAM_GB` | `64` | RAM floor (inclusive) |
| `TELEGRAM_ALERT_RULES` | unset (no signature rules) | JSON list of `{name, cpus[], min_ram_gb, ecc, min_nvme, max_price_eur}`; malformed → logged at ERROR every cycle and ignored, the global rule keeps working |
| `ALERT_STATE_KEY` | `alerted-listings.json` | Generation-relative filename for the dedup state |

The dedup identity is per listing, not per rule: a listing matching two rules
alerts once, and the message is prefixed with every matching rule's name
(`[i5-13500 64GB+ 2xNVMe] Hetzner auction: €61.70/mo …`). A global-only match
carries no prefix.

## Message content

Matching rule names (signature rules only), CPU, multi-thread benchmark score, RAM (with `ECC` when applicable), disk, datacenter, price, and a link
to `https://www.hetzner.com/sb` (Hetzner's auction page — there is no
verified stable deep link to an individual listing; the auction UI is a
client-side SPA over the same feed this pipeline reads, with no discovered
`?id=`/`#id` scheme). The message gives enough detail to identify the
listing once there.
