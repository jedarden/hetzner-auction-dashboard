# Architecture

_Accepted target architecture, 2026-08-22. Migration from the current
Pages-bundled publisher is pending._

The dashboard has two independently deployed halves joined by a versioned
Parquet contract:

```text
Hetzner public auction feed
          |
          | one GET/minute, with jitter and backoff
          v
single-writer pipeline Deployment
          |
          | write + verify immutable generation
          v
Garage: hetzner-auction/data/<generation>/...
          |
          | publish manifest.json last
          v
public read-only data origin
          |
          | CORS + HTTP range requests
          v
Cloudflare Pages static UI + DuckDB-WASM
```

## Static interface

Cloudflare Pages continues to host only `web/`: HTML, CSS, JavaScript, and
vendored/runtime assets. A code push may create a Pages deployment; a data
poll must never create one. The browser obtains the data origin from a small
configuration constant and fetches `manifest.json` with `cache: "no-store"`.

The Pages site remains independently deployable and continues to work without
a request-time application backend. DuckDB-WASM reads the selected immutable
Parquet generation directly from the data origin.

## Pipeline and publication

One long-lived, GitOps-managed Deployment polls Hetzner approximately once per
minute. It uses a single replica, slight scheduling jitter, and exponential
backoff for HTTP 429 and 5xx responses. A poll that produces no material data
change does not publish a new generation.

For a changed dataset, the pipeline:

1. Fetches and validates the Hetzner response.
2. Enriches listings and updates lifecycle/configuration history.
3. Writes all output files into a new immutable generation locally.
4. Verifies every Parquet/JSON file.
5. Uploads the complete generation to Garage.
6. Verifies the uploaded objects.
7. Replaces `manifest.json` last, making the generation visible atomically.

An example manifest is:

```json
{
  "schema_version": 1,
  "generation": "2026-08-22T18:31:00Z",
  "published_at": "2026-08-22T18:31:04Z",
  "files": {
    "snapshot": "generations/20260822T183100Z/current_snapshot.parquet",
    "listing_history": "generations/20260822T183100Z/listing_history.parquet",
    "config_history": "generations/20260822T183100Z/config_history.parquet",
    "unmatched_cpus": "generations/20260822T183100Z/unmatched-cpus.json"
  }
}
```

The manifest is the only mutable publication pointer. Readers therefore see
either the prior complete generation or the next complete generation, never a
mixture. Old generations are retained for a bounded rollback window and then
garbage-collected.

## Storage and serving

Garage object storage is the durable source of truth. The artifacts are
served through a dedicated public, read-only HTTPS origin such as
`hetzner-data.ardenone.com`; write access remains cluster-only. The origin
exposes only this dashboard's artifact prefix.

The data origin must support:

- `GET` and `HEAD`;
- byte-range requests (`Range`, `206`, `Content-Range`, `Accept-Ranges`);
- CORS restricted to the production Pages origin (and explicit development
  origins when needed);
- exposed `Content-Length`, `Content-Range`, `Accept-Ranges`, and `ETag`
  response headers;
- a short cache policy for `manifest.json` and long immutable caching for
  generation objects; and
- real 404 responses for missing objects, never an HTML SPA fallback.

A PVC is not part of the serving path. A rebuildable local cache may use a PVC
later if measurements justify it, but correctness and restart recovery must
come from Garage and the last published manifest.

## Failure behavior

Any failure before the final manifest replacement leaves the prior generation
live. A failed Hetzner poll, enrichment, local verification, upload, or remote
verification does not change the manifest. The client reports manifest/CORS/
Parquet failures explicitly and continues to use a generation it has already
loaded when possible. The `fetched_at` and `published_at` values expose stale
but otherwise valid data.

## Migration sequence

1. Provision the Garage prefix, writer credentials, and read-only data origin.
2. Add manifest-generation and Garage publishing to the pipeline while keeping
   the current Pages publisher available as a fallback.
3. Verify CORS, `HEAD`, and byte-range behavior through the public origin.
4. Teach the Pages client to resolve all files through `manifest.json`.
5. Run both publication paths until history continuity and browser loading are
   verified.
6. Remove per-cycle `wrangler pages deploy`; retain Pages deployment only for
   interface code changes.

This decision supersedes ADR-7's same-deployment data bundling. Historical
notes describing that implementation remain useful for migration context but
are not the target design.
