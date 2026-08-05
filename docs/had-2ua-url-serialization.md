# URL-Encoded Filter State Serialization (Bead had-2ua)

## Overview

This feature implements URL-encoded filter state serialization for the Hetzner Auction Dashboard. Users can now bookmark their filtered views or share URLs with specific filter configurations.

## Implementation Details

### Core Functions

#### 1. `serializeFiltersToURL()` (line 789)

Serializes the current filter state into URL query parameters without reloading the page.

**Serialized Parameters:**
- `preset` - Active preset ID (e.g., "game-server", "home-nas")
- `sort` - User sort axis override (e.g., "price_per_gb_ram")
- `cpu_family` - CPU family filter (ryzen, xeon, core)
- `min_ram` - Minimum RAM in GB
- `max_price` - Maximum monthly price in €
- `disk_type` - Disk type (nvme, ssd, hdd)
- `min_disk` - Minimum disk size in TB
- `location` - Datacenter location (fsn, nbg, hel, ash)
- `benchmark_matched_only` - Boolean flag for benchmark-matched filter
- `best_deal` - Boolean flag for Best Deal Now mode

**Implementation:**
```javascript
function serializeFiltersToURL() {
    const params = new URLSearchParams();
    
    // Serialize active state
    if (currentPreset) params.set('preset', currentPreset.id);
    if (userSortAxis !== 'auto') params.set('sort', userSortAxis);
    
    // Serialize individual filters
    // (only non-empty values are included)
    
    // Update URL without reloading
    window.history.pushState({ path: newURL }, '', newURL);
}
```

**Called on:**
- Preset selection changes
- Individual filter value changes
- Sort axis changes
- Best Deal Now toggle
- Reset filters

#### 2. `restoreFiltersFromURL()` (line 833)

Restores filter state from URL query parameters on page load or browser navigation.

**Restoration Logic:**
1. Check if any filter parameters exist
2. Set `isInitializingFromURL` flag to prevent recursive updates
3. Restore best deal mode state
4. Restore user sort axis
5. Check for preset parameter:
   - If found: apply preset (with location override if present)
   - If not found: restore individual filter values
6. Apply filtering and sorting
7. Replace history state to prevent back button loops

**Implementation:**
```javascript
function restoreFiltersFromURL() {
    const params = new URLSearchParams(window.location.search);
    
    // Guard clause for empty URLs
    if (!hasFilters) return false;
    
    isInitializingFromURL = true;
    
    try {
        // Restore best deal mode, sort axis, preset, or individual filters
        // (complex logic to handle preset vs individual filter precedence)
        
        // Always replace history to prevent going back to same URL
        window.history.replaceState({}, '', window.location.pathname + window.location.search);
    } finally {
        isInitializingFromURL = false;
    }
    
    return true;
}
```

**Called on:**
- Page initialization (line 1457)
- Browser back/forward navigation via `popstate` event (line 1473)

### State Management

#### `isInitializingFromURL` Flag (line 786)

Prevents URL serialization while restoring state from URL parameters. This ensures:

1. No redundant URL updates during restoration
2. No infinite loops between serialization and restoration
3. Clean state transitions

#### Browser History Integration

- **Push State** (line 829): `window.history.pushState()` updates URL without page reload
- **Replace State** (line 983): `window.history.replaceState()` prevents duplicate history entries
- **Pop State** (line 1470): Handles browser back/forward navigation

### Example URLs

```
# Ryzen servers with 64GB+ RAM under €80/month
index.html?cpu_family=ryzen&min_ram=64&max_price=80

# Game Server preset
index.html?preset=game-server

# Large storage servers (HDD, 4TB+)
index.html?disk_type=hdd&min_disk=4&sort=price_per_tb_disk

# Best Deal Now (single best-value server)
index.html?benchmark_matched_only=true&best_deal=true

# Home NAS preset with RAM-value sort override
index.html?preset=home-nas&sort=price_per_gb_ram

# Falkenstein NVMe servers with 32GB+ RAM (benchmark matched)
index.html?location=fsn&disk_type=nvme&min_ram=32&benchmark_matched_only=true
```

## Technical Design

### URL Parameter Design

**Naming Convention:** Snake_case for query parameters (matches common URL conventions)

**Parameter Values:**
- Text values: Lowercase (e.g., "ryzen", "nvme", "fsn")
- Numeric values: As-is (e.g., "64", "80.50")
- Boolean values: String "true"/"false" (e.g., "benchmark_matched_only=true")

**Empty Value Handling:** Only non-empty, non-zero values are serialized to keep URLs clean

### Precedence Rules

1. **Preset + Individual Filters**: If `preset` is specified, preset filters are applied first, then any individual filters override preset values
2. **Sort Override**: `sort` parameter always overrides preset default sort
3. **Best Deal Mode**: `best_deal=true` works independently of filters/presets

### Race Condition Prevention

The `isInitializingFromURL` flag prevents a common race condition:

```
User opens URL → restoreFiltersFromURL() → updates DOM → 
DOM change event → serializeFiltersToURL() → URL update → 
(triggered by same filter change)
```

With the flag, this becomes:

```
User opens URL → isInitializingFromURL = true → 
restoreFiltersFromURL() → updates DOM → 
DOM change event → check flag → skip serialize → 
isInitializingFromURL = false
```

## Testing

### Verification Page

Created `test-url-serialization-verification.html` with comprehensive tests:

1. ✅ Basic filter serialization
2. ✅ Preset with sort override
3. ✅ Boolean parameter encoding
4. ✅ Numeric parameter handling
5. ✅ Empty parameters handling
6. ✅ Special characters in parameters
7. ✅ Complete filter state serialization
8. ✅ Preset-only URL
9. ✅ URL encoding correctness
10. ✅ Empty URL (no filters)

### Manual Testing Workflow

1. **Test Filter Persistence:**
   - Set filters → verify URL updates → bookmark → refresh → verify filters restored

2. **Test Browser Navigation:**
   - Set filters → change filters → use back button → verify previous state restored

3. **Test Best Deal Mode:**
   - Enable Best Deal Now → bookmark → refresh → verify only best deal shown

4. **Test Preset Application:**
   - Select preset → verify URL → refresh → verify preset reapplied

## Benefits

1. **Bookmarkable Views**: Users can save their filtered searches as browser bookmarks
2. **Shareable URLs**: Users can copy URLs to share specific filtered views
3. **Back/Forward Support**: Browser navigation works seamlessly with filter state
4. **No Server Required**: All state is client-side in URL parameters
5. **Clean URLs**: Empty/zero values are omitted for readability

## Usage Notes

- URLs are self-contained and require no server-side session state
- Filter state survives browser refreshes and navigation
- Works seamlessly with existing preset system
- Compatible with future server-side data loading (no tight coupling to mock data)
