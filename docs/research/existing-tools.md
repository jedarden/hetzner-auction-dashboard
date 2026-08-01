# Existing tools for browsing/monitoring the Hetzner Server Auction

Survey of third-party tools, dashboards, bots, and libraries that already do some
version of "browse/filter/monitor Hetzner's dedicated server auction," done before
building this project's feature set. All entries were checked live in August 2026
(web search + page/README fetch + GitHub API for stars/last-push where applicable).
This market moves fast and repos disappear/rename often — treat "active" status as
a snapshot, not a guarantee.

---

## 1. Server Radar (a.k.a. "hetzner-radar")

- **URL(s):** https://radar.iodev.org/ (also mirrored at https://server-radar.pages.dev/)
- **Repo:** https://github.com/elsbrock/hetzner-radar
- **Status:** **Very active.** 366 stars, 16 forks, 919 commits, last push same day as
  this research (2026-08-01). By far the most popular and polished tool found in this
  survey.
- **Architecture note:** SvelteKit frontend + **DuckDB-WASM for client-side
  filtering**, with a Cloudflare Workers / GitHub Actions pipeline feeding Cloudflare
  D1 and R2. This is architecturally the closest sibling to the plan for this
  project (static frontend, DuckDB-WASM, no client-side joins) — see
  `docs/plan/plan.md` and `docs/notes/benchmark-priority.md`.
- **Features confirmed:**
  - Filter by CPU (model/core count), RAM, storage type/size, location/datacenter
  - Advertises itself as comparing auction prices against Hetzner's standard
    dedicated and cloud SKUs in one filterable view
  - **Price history:** ~3 months retained per configuration; shows lowest price ever
    seen for a given config, daily price index vs. a rolling baseline, min €/GB RAM,
    min €/TB storage, AMD vs. Intel and ECC vs. non-ECC spreads, listing volume by
    country/datacenter/CPU (see `/statistics` and `/guide` pages)
  - **Alerts:** target-price alerts via email and Discord webhook (with fallback
    logic), plus cloud-availability-by-location alerts
  - **Refresh:** auction data polled every 5 minutes via two parallel pipelines;
    cloud availability checked every 60 seconds; client-side DuckDB purges records
    older than 90 days
  - Free, open source, no login required to browse (accounts only needed for alerts)
- **Features NOT found:** no CPU benchmark scores (PassMark/Geekbench/anything) of
  any kind, no documented public API, no comparison/side-by-side view, no
  auto-buy/sniping. A LowEndTalk thread about this tool (202410) has no mention of
  benchmarking as a request or gap — discussion is purely positive/UI-focused.
- **Bottom line:** the category leader on breadth, history, and alerting — and a
  clean confirmation that **the most mature, most-used tool in this space has zero
  CPU-benchmark integration.**

## 2. Hetzner Auction Browser+ (a.k.a. "Hetzner Value Auctions")

- **URL(s):** https://auction.akua.dev/ (about page: `/about`); also republished at
  https://hetzner-value-auctions.cnap.tech/guide (`cnap.tech/blog/...` redirects to
  `akua.dev/blog/...`, same author)
- **Status:** Appears to be a single-developer side project; blog post (2026-04-29)
  cites ~36,400 page views and ~6,490 unique visitors "since January [2026]," so it
  has real usage but is much smaller than Server Radar. No GitHub repo was surfaced
  (may be closed-source or unlisted).
- **Features confirmed:**
  - **This is the clearest CPU-benchmark implementation found.** Live auction data
    is "enriched with PassMark CPU benchmark scores." Two explicit value metrics:
    **CPUMark/€** (raw processing power per euro) and a blended **Total Score**.
  - Total Score methodology (from the `/about` page): CPU performance via PassMark
    "enhanced with our own scaling algorithm," RAM scored with a 20% bonus for ECC,
    storage weighted NVMe=2x / SSD=1x / HDD=0.5x, plus network bandwidth and CPU TDP
    (efficiency) factored in
  - Filter by CPU performance range, RAM, price (with ~5% tolerance band), location
    (by country/region), and boolean requirements: IPv6, NVMe, ECC
  - Sort by price range and CPU-score-per-euro
  - Mentions a **server comparison view** (side-by-side), mobile-responsive, dark
    mode
  - Real-time pull from Hetzner's live auction API
- **Features NOT found:** no price history/trend tracking, no alerts/saved
  searches, no documented API, no auto-buy.
- **Bottom line:** the best precedent for "PassMark score joined onto live
  listings + blended value score," but it's a much smaller/less-maintained project
  than Server Radar, has no history, and (like everything else found) sources from
  PassMark only — never Geekbench.

## 3. hzfind

- **URL:** https://github.com/clouedoc/hzfind
- **Status:** Active-ish. 81 stars, 3 forks, last push 2026-05-23. Rust, MIT
  licensed, requires Rust 2024 edition.
- **Type:** CLI/TUI, not a web dashboard.
- **Features confirmed:**
  - Fetches live Hetzner auction data
  - **PassMark CPU benchmarks bundled directly in the binary** (no external API
    call needed to score a listing)
  - Filters: max monthly price (`p`), minimum CPU score (`s`), minimum core count
    (`c`)
  - Sort by CPU value (score/€), storage value (GB/€), RAM value (GB/€) — i.e. three
    separate price-per-metric rankings, not one blended score
  - Optional comparison against a Hetzner Cloud CCX33 baseline (€62.99/mo) to show
    when auction hardware beats renting cloud
- **Features NOT found:** no price history, no alerts, no comparison view beyond
  the single cloud baseline, no API, no location/network/disk-type filters
  documented, no auto-buy.
- **Bottom line:** second confirmed PassMark implementation, terminal-only, smaller
  in scope than Auction Browser+ (no blended score, no RAM/disk-type filtering
  documented) but notable for being genuinely CPU-score-first in its UX — sorting by
  value-per-euro is the primary interaction, not an add-on.

## 4. Server Auction Tracker (madfam-org)

- **Repo:** https://github.com/madfam-org/server-auction-tracker
- **Live site:** https://sniper.madfam.io (confirmed reachable — HTTP 200 via curl;
  WebFetch's own fetcher got a 403, likely bot/Cloudflare protection, so UI content
  couldn't be independently verified beyond the README's claims)
- **Status:** Small/early. **0 stars, 0 forks**, 69 commits, last push 2026-06-14.
  Treat feature claims below with more caution than the above tools since GitHub
  traction is essentially nil and the live UI wasn't independently rendered.
- **Features claimed (from README/docs):**
  - Filters: RAM, CPU cores, drives, price, datacenter, ECC, NVMe; **shareable
    filter URLs** for bookmarking
  - **Third confirmed PassMark integration**, covering "~90 processor models,"
    normalized against the highest-performing server in each scan; weighted scoring
    (CPU 0.25, RAM 0.20, storage 0.15, + other factors) scaled to a 0–100 score with
    "deal quality" labels
  - Price history via SQLite: min/max/avg stats per CPU model, deal-quality
    calculations
  - Dashboard charts: AMD vs. Intel price trends, datacenter distribution, top
    value CPUs
  - Alerts: Slack, Discord, Webhook, Telegram, min-score thresholds, curated
    daily/weekly digest
  - **Cluster simulation** (testing whether a set of servers meets a capacity
    target) — a genuinely unique feature not seen elsewhere
  - **Claimed auto-ordering**: Hetzner Robot API integration for automatic purchase
    with configurable safety gates and a "Buy Now" two-step confirmation flow, plus
    order audit logs
  - CLI tool (`foundry-scout`) in addition to the web dashboard (`deal-sniper`)
- **Bottom line:** on paper the single most feature-complete tool found — PassMark
  scoring, history, alerts, AND auto-buy — but the near-zero GitHub traction and
  inability to verify the live UI means this should be read as "most ambitious
  README," not confirmed best-in-class. If real, it's the only tool combining
  benchmark scoring with actual purchase automation.

## 5. Hetzner Server Auction Monitor (Apify actor)

- **URL:** https://apify.com/rl1987/hetzner-server-auction-monitor
- **Status:** Active, commercially hosted on Apify's platform (pay-per-use).
- **Features confirmed:**
  - Filters: max price (EUR/USD), min RAM, ECC-only toggle, disk type
    (NVMe/SATA/HDD) + min capacity, datacenter prefix (FSN/NBG/HEL/etc.),
    case-insensitive CPU model match
  - Alerts via generic JSON webhook or native Apify integrations; de-duplicates so
    each server triggers only one notification across polls
  - Configurable schedule, default every 10 minutes, cron-customizable
  - **Genuine API access** (it's an Apify Actor — JS/Python/REST/CLI clients all
    work) at $1 per 1,000 checks (~$1.44–$4.32/month at typical polling rates)
- **Features NOT found:** no sorting, no price-per-metric, no CPU benchmarks, no
  price history, no comparison view.
- **Bottom line:** a pure filter+webhook monitor sold as infrastructure, not a
  dashboard. Useful data point that "API access" as a feature exists in this space,
  just not bundled with any analysis features.

## 6. hetzner-auction-discord-bot (Quintenvw)

- **URL:** https://github.com/Quintenvw/hetzner-auction-discord-bot
- **Status:** Active. 37 stars, 4 forks, last push 2026-04-27.
- **Type:** Discord bot, MongoDB-backed per-user filter configs.
- **Features confirmed:** filter by CPU type, RAM (capacity + ECC), storage
  (HDD size/quantity/type), location, price threshold with VAT calc, currency
  (EUR/USD); posts direct links and @-mentions matching users; stale configs
  auto-expire after 90 days of no matches.
- **Features NOT found:** no sorting, no price-per-metric, no CPU benchmarks, no
  history, no comparison, no API, no auto-buy.
- **Bottom line:** solid, actively-used notification bot; pure filter-and-ping, no
  analysis layer.

## 7. hetzner-notify (RickBakkr)

- **URL:** https://github.com/RickBakkr/hetzner-notify
- **Status:** **Dead.** 27 stars, 6 forks, last push 2018-10-06 (~8 years stale).
  Included for completeness / to show how much churn this niche has — most tools
  from the 2018-era Hetzner "Serverbörse" webhook-bot wave are abandoned.
- **Features:** webhook-based notifications of new auction listings. No benchmark,
  history, or filtering sophistication documented beyond basic matching.

## 8. hetzner-auction-hunter (danielskowronski)

- **URL:** https://github.com/danielskowronski/hetzner-auction-hunter
- **Status:** Semi-stale. 108 stars, 23 forks (highest star count of the
  notification-bot category), but last push 2024-08-09 — no activity in ~2 years.
- **Type:** CLI/cron notification tool, very broad notification-channel support
  (Pushover, SimplePush, Slack, Gmail, SMTP, Telegram, Gitter, Pushbullet, Join,
  Zulip, Twilio, PagerDuty, Mailgun, PopcornNotify, StatusPage.io, iCloud,
  VictorOps) via the `notifiers` library.
- **Features confirmed:** filter by max price, disk count/capacity/min-per-disk
  size, SSD/NVMe requirement, CPU count, RAM, ECC, hardware RAID, redundant PSU,
  discrete GPU, IPv4 availability, Intel NIC, datacenter/region; configurable VAT
  (defaults 19% DE); dedup via local state file; JSON payload passthrough; dry-run
  test mode.
- **Features NOT found:** no CPU benchmarks, no price history, no comparison view,
  no documented API, no auto-buy. Refresh depends entirely on user's own cron
  setup.
- **Bottom line:** the most exhaustive raw hardware-filter list of any tool
  surveyed (RAID, PSU, GPU, NIC vendor) — a good reference for "what filter fields
  power users actually want" even though it's stale and has zero
  performance-normalization.

## 9. hetzner-auction (robpickerill)

- **URL:** https://github.com/robpickerill/hetzner-auction
- **Status:** **Dead / minimal.** 1 star, 0 forks, 16 commits, last push
  2020-11-01.
- **Features:** posts servers of interest to Slack on a daily Lambda schedule.
  No filters, sorting, benchmarks, history, or API documented — essentially a
  personal utility script.

## 10. hetzner-cli (ytspar)

- **URL:** https://github.com/ytspar/hetzner-cli
- **Status:** Active. 0 stars/forks but last push 2026-07-15 (very recent);
  positioned as a "feature-complete" general Robot API CLI/Node library, not
  auction-specific.
- **Features confirmed (auction subcommands):**
  - Rich filters: price (min/max monthly, hourly cap, setup fee), fixed-price vs.
    true-auction distinction, CPU model match, socket/core counts, RAM + ECC,
    total disk capacity range, drive count, drive type (NVMe/SATA/HDD), datacenter,
    bandwidth minimum, GPU presence, Intel NIC, high-I/O flag
  - **Sorting** across the widest field list found in this survey: price, hourly,
    setup, ram, disk, disk_count, cpu, cpu_count, datacenter, bandwidth,
    next_reduce (asc/desc), plus `--limit`
  - `--json` output for scripting/`jq` piping; a `--direct` flag to bypass its own
    15-minute hosted cache; `auction status`, `auction diff` (snapshot comparison),
    and `auction watch` (poll for changes) subcommands; offline fallback cache with
    stale-data warnings on stderr
  - Exports filtering/sorting/fetch functions as a library for Node automation
- **Features NOT found:** no CPU benchmark scoring, no price history persistence
  beyond diff/watch, no alerts/notification delivery, no comparison view, no
  auto-buy.
- **Bottom line:** the most complete raw filter+sort+fetch primitive of anything
  surveyed, and a good sanity check on "what sort dimensions are table stakes" —
  but it's explicitly a low-level API client, not a decision-support tool, and has
  no performance normalization at all.

## 11. Get Hetzner (gethetzner.com)

- **URL:** https://gethetzner.com/products/auction/
- **Status:** Live commercial site.
- **Type:** Not a monitoring tool — a **reseller/marketplace front-end** for
  Hetzner-class hardware (EX/AX/SX/GPU/Dell lines), claiming 5-minute inventory
  refresh and offering filters for CPU type, RAM, storage/disk config. No CPU
  benchmark data. Included because it surfaced repeatedly in searches and could be
  mistaken for an auction dashboard; it's adjacent, not a direct comparator.

---

## Adjacent but out-of-scope (context, not direct competitors)

- **achromatic.dev — "Hetzner Server Comparison 2026" (blog post):** a *static*
  article, not a live tool, that does join PassMark (dedicated) and a cloud-VPS
  benchmark score to Hetzner's **standard catalog** pricing — but it **explicitly
  excludes the Server Auction** ("this comparison intentionally excludes servers
  listed on Hetzner's Server Auction platform, which employs a descending-price
  Dutch auction mechanism"). This is a useful signal: even a
  benchmark-comparison-focused writer treated the auction's constantly-rotating,
  heterogeneous CPU inventory as too much of a moving target for a static join —
  which is exactly the kind of pipeline work this project is committing to
  automate.
- **ServerHunter (serverhunter.com)** and **serverlist.dev:** multi-provider
  (79,000+ servers across ~930 hosting companies) price/availability comparison
  engines. Long-running (ServerHunter since 2013/2018), 24-hour refresh, stock
  alerts every 15 minutes. Not Hetzner-auction-specific and no evidence of CPU
  benchmark integration — they compare list-price hosting plans generally, not
  the Dutch-auction mechanic specifically.
- **Geekbench Browser (browser.geekbench.com) / VPSBenchmarks / BareMetalBench:**
  these are where actual Geekbench/YABS benchmark *runs* for specific
  Hetzner-auction-purchased machines end up — individual buyers post results after
  the fact (e.g. "Hetzner Server Auction i5-12500," "Hetzner - Auction Server Yab
  October 2024"). This is real Geekbench data that exists in the wild tied to
  Hetzner auction hardware, but it is **disconnected, manual, and after-the-fact**:
  nobody has joined this data back onto the *live* auction feed for filtering or
  sorting. It's a corpus that a future benchmark-matching pipeline could
  potentially mine, not a competing product.
- **API client libraries** (`hrobot-rs`, `aszlig/hetzner`, `Radiergummi/hetzner-api-client`,
  `nl2go/hetzner-robot-api-mock`): general-purpose Hetzner Robot API clients in
  Rust/Python/Node used to *build* tools like the above. Not end-user products
  themselves; noted only because they surfaced heavily in searches for
  "Hetzner auction JSON parser."
- **Official Hetzner Server Auction FAQ** (docs.hetzner.com): confirms the native
  UI supports basic filtering (price, RAM, drive count, free-text like "Epyc" /
  "Xeon" / "DDR4") but documents no sorting beyond that, no API, no refresh-rate
  disclosure, and no performance/benchmark data of any kind — the floor every
  third-party tool is improving on.

---

## Feature landscape: table-stakes vs. differentiating vs. gaps

### Table stakes (present in nearly every tool surveyed)
- Filter by **price**, **RAM**, **disk type/size**, **CPU model or core count**,
  and **location/datacenter** — every single tool above implements this core set.
- Some form of **alerting/notification** (email, Discord, Slack, Telegram,
  webhook, Pushover, etc.) is present in 7 of the 11 direct tools — it's more
  common than not, especially among the bot-style tools.
- Polling cadence in the **5–15 minute** range is the norm across every tool that
  documents it (Server Radar: 5 min; gethetzner.com: 5 min; Apify actor default:
  10 min; hetzner-cli hosted cache: 15 min) — this roughly tracks how often
  Hetzner's own Dutch-auction prices step down.
- ECC and NVMe/SSD/HDD as explicit boolean/enum filters are near-universal once a
  tool has any disk/RAM filtering at all.

### Common, but not universal
- **Price-per-metric** (€/GB RAM, €/TB storage) — present as an aggregate stat in
  Server Radar's `/statistics` page, and as a per-listing sort key in hzfind
  (GB/€ for RAM and storage), but most of the notification-bot-style tools skip it
  entirely.
- **Price history / trend tracking** — Server Radar (3 months) and
  Server Auction Tracker (SQLite, claimed) have it; most others (Auction Browser+,
  hzfind, all the bots) do not.
- **Sorting** beyond simple price ascending — well-developed in hetzner-cli
  (11 sort keys) and hzfind (3 value-based sorts), largely absent from the
  notification bots, which only match/alert rather than rank.
- **Shareable/saved filter configurations** — Server Auction Tracker's shareable
  URLs and the Discord/Apify bots' persisted per-user filters are the only
  examples; most web dashboards don't appear to persist a "saved search" concept
  beyond an alert rule.
- **Genuine API access** — only the Apify actor (paid, general-purpose) and
  hetzner-cli / hrobot-rs (open-source client libraries, not hosted APIs) qualify.
  Neither of the two leading dashboards (Server Radar, Auction Browser+) exposes a
  documented public API for third parties to build on.

### Rare / differentiating
- **CPU benchmark score joined onto listings** — found in only **3 of 11** direct
  tools (Auction Browser+, hzfind, Server Auction Tracker), and **all three use
  PassMark exclusively**; none use Geekbench, SPEC, or any multi-source benchmark.
  Notably, the single most popular and actively maintained tool in the entire
  space (Server Radar, 366 stars, updated the day of this research) has **no**
  benchmark integration at all — the market leader on every other axis (history,
  alerts, polish, maintenance) skips the one thing this project is centered on.
- **Blended "value score"** combining CPU + RAM + storage + network into one
  number — only Auction Browser+ (public methodology: PassMark + ECC bonus +
  storage-type weighting + bandwidth/TDP) and Server Auction Tracker (claimed:
  weighted 0.25/0.20/0.15/... blend, 0-100 scale) attempt this; hzfind deliberately
  keeps CPU/RAM/storage value-per-euro as three separate numbers rather than
  blending them.
- **Comparison/side-by-side views** — only Auction Browser+ mentions this.
- **Cluster/fleet capacity simulation** — unique to Server Auction Tracker
  (claimed), no other tool attempts it.
- **Auto-buy / sniping** — only Server Auction Tracker claims this (Hetzner Robot
  API order automation with safety gates), and it's the least-verified tool in the
  survey (0 GitHub stars, UI not independently renderable). No other tool
  attempts automated purchasing; everything else stops at "notify a human."
- **Browser extension** — none found. Every tool in this space is either a
  standalone web dashboard, a CLI/TUI, or a chat bot. A Chrome/Firefox extension
  that overlays benchmark scores directly on Hetzner's own auction page appears to
  be unexplored territory.

### Where the CPU-benchmark angle specifically stands
Three small/medium tools (Auction Browser+, hzfind, Server Auction Tracker) have
already proven the *core idea* — joining PassMark scores onto live auction
listings and sorting by value-per-euro is technically straightforward and has
been done, more than once, independently. So the raw concept is **not a blue-ocean
gap**. But every existing implementation has real limitations this project can
target:

1. **Single-benchmark-source, unverified coverage.** All three use PassMark only;
   the one that discloses coverage (Server Auction Tracker) covers "~90 processor
   models" — likely incomplete given how many distinct refurb CPU SKUs rotate
   through the auction. None cross-check against Geekbench, and the real
   Geekbench/YABS data that does exist for Hetzner auction hardware
   (browser.geekbench.com, VPSBenchmarks, BareMetalBench) sits disconnected from
   any of these tools — it's a corpus nobody has mined back into a live feed.
2. **Benchmark integration and "tool quality" haven't coincided.** The tool with
   the best benchmark implementation (Auction Browser+) has no price history and
   modest traffic; the tool with the best history/alerts/polish (Server Radar) has
   no benchmark at all. Nobody has combined "Server-Radar-grade maintenance and
   data depth" with "PassMark-grade value scoring."
3. **No performance-normalized history or alerting.** Every history feature found
   tracks raw €, not €-per-benchmark-point over time; every alert feature found
   thresholds on raw price/spec, not on a value score. "Alert me when €/PassMark
   drops below X" does not appear to exist anywhere in this survey.
4. **No workload-aware scoring.** Auction Browser+'s Total Score and Server
   Auction Tracker's weighted score are each a single fixed blend; nothing lets a
   user weight single-thread vs. multi-thread performance (or CPU vs. RAM vs.
   storage) for their own workload.
5. **Even benchmark-aware, non-auction tooling treats the auction as too volatile
   to bother with** (achromatic.dev explicitly excludes it) — which is really a
   statement that the auction's constant CPU-inventory churn makes maintaining an
   accurate benchmark join genuinely hard, i.e. it's a real moat, not just
   unclaimed territory. This validates `docs/notes/benchmark-priority.md`'s framing
   that the benchmark join, done well and kept accurate, is the one piece of real
   engineering work here — everything else (filter/sort UI, price display) is
   solved territory that Server Radar and hetzner-cli already demonstrate how to
   do well.

**Net assessment:** CPU-benchmark-joining on Hetzner auction listings is a proven,
replicated idea (3 independent implementations), so it is not a novel concept —
but it has never been done at the data-quality/breadth level (multi-source
benchmarks, verified CPU coverage) or combined with the feature depth (price
history, alerting, maintenance cadence) that the market leader in every other
dimension (Server Radar) already has. The gap is not "benchmark scores don't
exist elsewhere," it's "nobody has paired good benchmark data with a
well-maintained, full-featured dashboard, and nobody normalizes history/alerts by
performance rather than raw price."
