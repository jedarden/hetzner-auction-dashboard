# User-Selectable Primary Sort Axis Implementation (had-1vp)

## Summary
Implemented user-selectable primary sort axis feature, allowing RAM- or storage-heavy buyers to promote `price_per_gb_ram` or `price_per_tb_disk` to the primary sort instead of the default `price_per_benchmark_point_multi`.

## Changes Made

### 1. UI Components Added
- **Sort Selector Dropdown**: Added a new dropdown in the filters section with 5 options:
  - Auto (use preset default) - maintains backward compatibility
  - Benchmark Value (multi-thread) - price_per_benchmark_point_multi
  - Benchmark Value (single-thread) - price_per_benchmark_point_single  
  - Value per GB RAM - price_per_gb_ram
  - Value per TB Disk - price_per_tb_disk

- **Contextual Description**: Added dynamic help text that explains each sort option's use case

### 2. CSS Styling
- `.sort-selector`: Flex container with top border for visual separation
- `.sort-description`: Smaller text for contextual help
- Consistent styling with existing filter controls

### 3. JavaScript Logic
- Added `userSortAxis` variable to track user selection (default: 'auto')
- Updated `filterAndSortListings()` to respect user selection when not 'auto'
- Added event listener for sort-axis dropdown that:
  - Updates the userSortAxis variable
  - Clears active preset when user manually selects (consistent with existing behavior)
  - Triggers resort with new sort method
- Added `updateSortDescription()` function with contextual help for each option
- Updated `updateSortLabel()` to show effective sort method considering user override
- Updated `applyPreset()` to reset userSortAxis to 'auto' when preset is applied
- Updated `resetFilters()` to reset userSortAxis to 'auto'

## Philosophy Alignment
This implementation follows ADR-3's "separate metrics, user decides priority" philosophy:
- Benchmark value remains the documented default (auto mode)
- Users can opt-in to RAM- or disk-first sorting
- Preset configurations continue to work as before
- Manual user selection takes precedence over preset defaults

## User Experience
1. **Default Behavior**: Works exactly as before - preset defaults are used
2. **Manual Override**: User can select a specific sort axis which takes precedence
3. **Smart Reset**: Changing sort axis clears active preset (consistent with filter changes)
4. **Contextual Help**: Each option shows when/why to use it
5. **Visual Feedback**: Current sort method is displayed in results section

## Testing
The implementation works with existing mock data and handles all 5 sort modes correctly, including:
- Proper handling of null benchmark values (sorted last)
- Correct sort direction (ascending)
- Proper integration with filter system
- Consistent behavior with preset selection/reset
