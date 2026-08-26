# Telegram threshold alerting

The pipeline sends a Telegram alert (via [telegram-relay](https://git.ardenone.com/jedarden/telegram-relay),
an internal HTTP relay deployed alongside this pipeline on ardenone-cluster)
whenever a listing's `price_effective_monthly` drops under a configured
threshold — the same field the dashboard UI shows as "Price/mo*".

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
with different specs (EC-4, see `enricher.py`'s `build_config_signature`
docstring), so `listing_id` alone would misidentify a since-changed listing
as "already alerted."

## Configuration

All in `pipeline-config` (`k8s/ardenone-cluster/hetzner-auction-dashboard/configmap.yml`):

| Var | Default | Purpose |
|---|---|---|
| `TELEGRAM_RELAY_URL` | in-cluster `telegram-relay` Service | Where to POST `{text}` |
| `TELEGRAM_ALERT_MAX_PRICE_EUR` | `59.00` | Alert threshold |
| `ALERT_STATE_KEY` | `alerted-listings.json` | Generation-relative filename for the dedup state |

## Message content

CPU, RAM, disk, datacenter, price, and a link to
`https://www.hetzner.com/sb` (Hetzner's auction page — there is no verified
stable deep link to an individual listing; the auction UI is a client-side
SPA over the same feed this pipeline reads, with no discovered `?id=`/`#id`
scheme). The message gives enough detail to identify the listing once there.
