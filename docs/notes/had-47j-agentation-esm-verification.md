# Agentation CDN ESM Build Verification (had-47j)

## Task Completed

Verified that agentation publishes a CDN-consumable ESM build and demonstrated how to mount it in an isolated React root, per ADR-5.

## Verification Results

### ✅ ADR-5 Invalidation Trigger Confirmed

Agentation **DOES** publish a CDN-consumable ESM build:

```bash
$ npm info agentation
agentation@3.0.2 | PolyForm-Shield-1.0.0

$ npm pack agentation --pack-destination /tmp
# Tarball contents:
package/dist/index.mjs       # ESM module
package/dist/index.d.mts    # TypeScript declarations for ESM
package/dist/index.js       # CommonJS fallback
package/dist/index.d.ts     # TypeScript declarations for CJS
```

**Package exports:**
```json
{
  "main": "./dist/index.js",
  "module": "./dist/index.mjs",
  "types": "./dist/index.d.ts",
  "exports": {
    ".": {
      "types": "./dist/index.d.ts",
      "import": {
        "types": "./dist/index.d.mts",
        "default": "./dist/index.mjs"
      },
      "require": {
        "types": "./dist/index.d.ts",
        "default": "./dist/index.js"
      }
    }
  }
}
```

### ✅ CDN Availability

Agentation is available via esm.sh as an ESM module:

```javascript
import { Agentation } from 'https://esm.sh/agentation@3.0.2';
```

## Integration Approach

### Isolated React Root Pattern

The dashboard remains plain HTML/JS with no build step. Agentation is mounted in a completely isolated React root loaded via CDN:

```html
<!-- Agentation container - isolated from dashboard -->
<div id="agentation-root"></div>

<!-- Load React 18 and agentation from CDN as ESM -->
<script type="module">
    import React from 'https://esm.sh/react@18.3.1';
    import { createRoot } from 'https://esm.sh/react-dom@18.3.1/client';
    import { Agentation } from 'https://esm.sh/agentation@3.0.2';

    const agentationRoot = document.getElementById('agentation-root');
    const root = createRoot(agentationRoot);
    root.render(React.createElement(Agentation, {
        onAnnotationAdd: (annotation) => {
            console.log('Annotation:', annotation);
        }
    }));
</script>

<!-- Dashboard's own JavaScript (completely independent) -->
<script>
    // Plain JS for filters, sorts, DuckDB-WASM queries
    // No dependency on Agentation or React
</script>
```

### Key Properties

1. **Zero Dashboard Impact**: Removing Agentation has no effect on:
   - Filter/sort functionality
   - Data loading (DuckDB-WASM Parquet reads)
   - Any dashboard state or logic

2. **No Build Step**: Everything loads via CDN ESM imports — no npm install, no bundler, no framework adoption for the dashboard itself.

3. **Removable**: Delete the `<div id="agentation-root">` and the Agentation `<script>` block, and the dashboard continues working unchanged.

4. **Framework Boundary**: React is only used for Agentation's toolbar. The dashboard itself (filters, sorts, data display) stays pure HTML/JS as decided in the plan's "no framework" decision.

## Proof of Concept

See `web/agentation-test.html` for a working example demonstrating:

- Plain HTML/JS dashboard with filters and listings
- Agentation toolbar mounted in isolated React root via CDN
- Dashboard JavaScript completely independent of Agentation
- Both coexisting without interference

## ADR-5 Status

**Decision validated** — no fallback to rejected alternatives (a) or (b) needed. The invalidation trigger was:

> "if `agentation` turns out not to publish a CDN-consumable ESM build (unverified as of this writing — confirm early in Phase 5, before committing further to this approach), fall back to rejected alternative (a) or (b)"

**Result**: Agentation DOES publish a CDN-consumable ESM build. ADR-5's chosen approach stands.

## Implementation Notes for Phase 5

When implementing the full dashboard:

1. Load React 18 and agentation from CDN as shown above
2. Mount Agentation in its own root (`#agentation-root`)
3. Keep all dashboard logic (filters, sorts, DuckDB-WASM) in plain JavaScript
4. Use Agentation callbacks if integrating with agent workflows, or omit for standalone use
5. The isolated root pattern means Agentation can be added/removed without touching dashboard code

## Why This Matters

This verification confirms that the workspace's standard UI-feedback tool can be included **without** compromising the carefully-scoped "no framework" decision for the dashboard itself. The dashboard stays cheap, simple, and fast — Agentation just rides along in its own little React bubble at the bottom-right corner, exactly as ADR-5 intended.

---

**Date**: 2026-08-02
**Bead**: had-47j
**Plan Reference**: docs/plan/plan.md → ADR-5
