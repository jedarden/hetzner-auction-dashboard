# Hetzner Auction Feed: Real Endpoint + Schema (discovered 2026-08-06)

**Why this file exists**: Phase 1's fetcher was built against an assumed schema
(`docs/plan/plan.md`'s original Dependency Integration Contracts entry pointed
at `robot.hetzner.com`'s Robot API / a legacy `/wird/json.pl` endpoint) that
does not match reality — neither endpoint returns the actual public auction
feed in the shape the fetcher expects. This is EC-2 (Feed schema change) from
`docs/plan/plan.md`'s Edge Case Catalog, except the mismatch was there from
the start rather than a live drift. This note is the authoritative reference
for the corrected endpoint and schema; the fetcher rewrite beads point here
instead of re-deriving it.

## Endpoint

```
GET https://www.hetzner.com/_resources/app/data/app/live_data_sb.json
```

- Public, unauthenticated, plain JSON. No Robot API auth needed (matches the
  plan's existing "no write/order calls, no Robot API authentication needed"
  stance in Dependency Integration Contracts — that constraint was already
  correct, only the URL was wrong).
- Response envelope: `{"server": [...], "filter": {...}, "serverCount": N}`.
  The fetcher's existing schema-detection in `_parse_response` (checks for a
  `"server"` key holding a list) already matches this correctly — **no change
  needed there**, only in how each item under `server` is parsed.

## Example listing (from a live pull, 2026-08-06)

```json
{
  "Id": 3050605,
  "Hardware": {
    "CPU": { "Name": "Intel XEON E-2176G", "CoreCount": 1 },
    "RAM": { "RealSize": 32768, "Size": 64, "SizeUnit": "GB", "Amount": 2, "ecc": true },
    "Storage": {
      "RealSize": 24576, "Size": 6144, "SizeUnit": "GB", "Amount": 4,
      "Disks": ["6.0 TB Enterprise HDD", "6.0 TB Enterprise HDD", "960 GB Datacenter SSD", "960 GB Datacenter SSD"],
      "Details": {
        "nvme": [960, 960],
        "sata": [],
        "hdd": [6000, 6000],
        "general": [960, 6000]
      }
    }
  },
  "Prices": {
    "monthly": { "EUR": 72, "USD": 80 },
    "hourly": { "EUR": 0.1154, "USD": 0.1282 },
    "setup": { "EUR": 0, "USD": 0 },
    "fixed": false
  },
  "IPPrices": { "monthly": { "EUR": 1.7, "USD": 1.9 }, "hourly": { "EUR": 0.0027, "USD": 0.003 }, "Amount": 1 },
  "Details": {
    "Description": ["IPv4", "iNIC", "ENT.HDD", "..."],
    "Information": ["2 x RAM 32768 MB DDR4 ECC", "2 x HDD SATA 6,0 TB Enterprise", "2 x SSD U.2 NVMe 960 GB Datacenter", "NIC 1 Gbit - Intel I219-LM"],
    "Specials": ["IPv4", "iNIC", "ECC"],
    "Traffic": "unlimited",
    "Bandwidth": 1000,
    "OS": ["Rescue system"],
    "Datacenter": { "Name": "FSN1-DC16", "Datacenter": "#FSN1-DC16" }
  },
  "Timer": { "ReduceNext": 133690, "ReduceNextHr": true, "ReduceNextTimestamp": 1786156152 }
}
```

Top-level keys present on every item, confirmed across all 80 listings in a
live pull: `Id`, `Hardware`, `Prices`, `IPPrices`, `Details`, `Timer`.

## Field mapping — raw feed → `RawListing` / `DiskSpec` (`pipeline/src/pipeline/fetcher.py`)

| `RawListing` field | Source path | Notes |
|---|---|---|
| `listing_id` | `str(Id)` | `Id` is a JSON integer, not a string — cast it. |
| `cpu_raw` | `Hardware.CPU.Name` | e.g. `"Intel XEON E-2176G"`. `Hardware.CPU.CoreCount` was `1` on every sampled listing regardless of actual core count — looks like a socket count, not a core count; don't use it for anything. |
| `ram_gb` | `Hardware.RAM.Size` | Already in GB (`SizeUnit` confirms `"GB"`). `RealSize` is per-DIMM capacity in MB (`RealSize × Amount / 1024 == Size`) — not needed. |
| `ram_ecc` | `Hardware.RAM.ecc` | Already boolean. |
| `datacenter` | `Details.Datacenter.Name` | e.g. `"FSN1-DC16"`. |
| `location` | existing `_extract_location_from_dc(datacenter)` helper, unchanged | First 3 chars of the datacenter name, uppercased — already produces `"FSN"`/`"NBG"`/`"HEL"`, matching `filter.location.values`' top-level city codes in the live response. No change needed to this helper. |
| `available_from` | **not present in this feed** | See "Removed: `available_from`" below. |
| `uplink_speed` | `Details.Bandwidth` | Integer Mbit/s, e.g. `1000`. |
| `price_base` | `round(Prices.monthly.EUR * 100)` | `Prices.monthly.EUR` is a plain float/int in whole EUR (e.g. `72`, or `60.7` per `filter.price.lowest.EUR` in the envelope) — **never** a `"€NN.NN"` string. Convert to cents by `× 100` and round. |
| `price_ipv4_monthly` | `round(IPPrices.monthly.EUR * 100)` | Required primary IPv4 charge published separately from the server base price. Include it in recurring and derived cost calculations. |
| `price_setup_fee` | `round(Prices.setup.EUR * 100)` | Same conversion; `0` when no setup fee. |
| `fetched_at` | pipeline-generated at fetch time | Unchanged. |

`disks: list[DiskSpec]` — from `Hardware.Storage.Details`:

- Only three keys matter: `hdd`, `sata`, `nvme` — each a flat list of **per-disk
  capacity in GB**, one entry per physical disk (not pre-grouped). Ignore
  `general` — it's a redundant flattened union of the other three, not a
  fourth disk category.
- Type mapping: `hdd` → `"HDD"`, `sata` → `"SSD"` (Hetzner's SATA auction
  drives are SSDs, confirmed against `Details.Information`'s human-readable
  strings, e.g. `"2 x SSD U.2 NVMe 960 GB"` / `"2 x HDD SATA 6,0 TB"` — `sata`
  in `Details` never means spinning disk), `nvme` → `"NVMe"`.
- Group each type's flat capacity list into `DiskSpec(type, count, capacity_gb)`
  by identical capacity value: e.g. `hdd: [6000, 6000]` → one
  `DiskSpec(type="HDD", count=2, capacity_gb=6000)`. If a type has disks of
  different capacities (e.g. `sata: [500, 250]`), emit one `DiskSpec` per
  distinct capacity — this already matches `docs/plan/plan.md`'s Data Models
  note that `disks` is "one struct per distinct disk type/size group in the
  listing," so no plan change is needed there, just the parser.

## Removed: `available_from`

The original `RawListing.available_from` field (and the old test fixtures'
`"available_from": "2026-08-03T10:00:00Z"` case) assumed Hetzner exposes a
future-availability window per listing. **No such field exists anywhere in
this feed.** The closest thing, `Timer.ReduceNext` /
`Timer.ReduceNextTimestamp`, is a countdown to the *next scheduled price
drop*, not an availability delay — auction listings in this feed are, by
construction, immediately orderable inventory. `available_from` should be set
to `None` unconditionally rather than sourced from anything, until/unless a
real availability-delay signal turns up. See `docs/plan/plan.md` Data Models
for the corresponding plan-level note.

## Filter/metadata block (`filter`, top-level)

Not consumed by the pipeline (v1 only needs `server`), but useful reference:
`filter.location.values` lists every datacenter code (`FSN1-DC*`, `NBG1-DC*`,
`HEL1-DC*`) plus the three city-level codes (`NBG`, `FSN`, `HEL`) confirming
the existing `_extract_location_from_dc` 3-char-prefix approach is correct.
`filter.price.{min,max,lowest}.EUR` confirms prices are always plain EUR
numbers, never cent-integers or currency-symbol strings.
