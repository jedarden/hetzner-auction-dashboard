/**
 * Snapshot Diff System for Hetzner Auction Dashboard
 *
 * Client-side (IndexedDB) comparison of "what changed since I last looked"
 * Caches the previous fetched snapshot and diffs row-by-config-signature against the current one.
 * Gets partial price-history value weeks before v2's full historical-stats architecture ships.
 *
 * Bead: had-33l
 */

// IndexedDB configuration
const DB_NAME = 'hetzner-auction-dashboard';
const DB_VERSION = 1;
const STORE_NAME = 'auction-snapshots';
const DISMISSED_DIFF_KEY = 'hetzner-auction-dismissed-diff';

/**
 * Generate a unique configuration signature for a listing
 * This signature identifies the "same" server configuration across different fetches
 *
 * Signature components:
 * - CPU model (normalized)
 * - RAM amount
 * - Disk configuration (type, count, total capacity)
 * - Location
 *
 * Price is EXCLUDED from signature - we track price changes separately
 * Listing ID is EXCLUDED - we track new/removed listings by signature, not ID
 */
function generateConfigSignature(listing) {
    const cpuKey = normalizeCpuModel(listing.cpu_model);
    const ramKey = `${listing.ram_gb}GB`;

    // Sort disks by type and size for consistent signature
    const diskKey = listing.disks
        .map(d => {
            // Handle both size_gb and size_tb formats
            const sizeInGB = d.size_gb || (d.size_tb ? d.size_tb * 1000 : 0);
            const count = d.count || 1;
            return `${d.type}:${count}x${sizeInGB}GB`;
        })
        .sort()
        .join(',');

    const locationKey = listing.location || 'unknown';

    return `${cpuKey}|${ramKey}|${diskKey}|${locationKey}`;
}

/**
 * Normalize CPU model string for consistent signatures
 * Handles variations in spacing, casing, and branding
 */
function normalizeCpuModel(cpuRaw) {
    return cpuRaw
        .toLowerCase()
        .replace(/\s+/g, ' ')
        .replace(/(amd|intel)\s+/g, '')
        .replace(/\(r\)/g, '')
        .replace(/\(tm\)/g, '')
        .trim();
}

/**
 * IndexedDB operations for snapshot storage
 */
const SnapshotDB = {
    /**
     * Open IndexedDB and create object store if needed
     */
    async open() {
        return new Promise((resolve, reject) => {
            const request = indexedDB.open(DB_NAME, DB_VERSION);

            request.onerror = () => reject(request.error);
            request.onsuccess = () => resolve(request.result);

            request.onupgradeneeded = (event) => {
                const db = event.target.result;

                // Create object store for snapshots
                if (!db.objectStoreNames.contains(STORE_NAME)) {
                    const store = db.createObjectStore(STORE_NAME, { keyPath: 'timestamp' });
                    store.createIndex('timestamp', 'timestamp', { unique: true });
                }
            };
        });
    },

    /**
     * Save a snapshot to IndexedDB
     */
    async saveSnapshot(listings) {
        const db = await this.open();
        const transaction = db.transaction([STORE_NAME], 'readwrite');
        const store = transaction.objectStore(STORE_NAME);

        // Create snapshot record
        const snapshot = {
            timestamp: Date.now(),
            listings: listings.map(listing => ({
                id: listing.id,
                cpu_model: listing.cpu_model,
                cpu_cores: listing.cpu_cores,
                ram_gb: listing.ram_gb,
                disks: listing.disks,
                price_effective_monthly: listing.price_effective_monthly,
                location: listing.location,
                benchmark_multi: listing.benchmark_multi,
                benchmark_single: listing.benchmark_single,
                benchmark_matched: listing.benchmark_matched
            }))
        };

        return new Promise((resolve, reject) => {
            const request = store.put(snapshot);

            transaction.oncomplete = () => {
                // Clean up old snapshots (keep only the most recent)
                // Note: cleanupOldSnapshots creates its own transaction
                this.cleanupOldSnapshots();
                resolve(snapshot);
            };
            transaction.onerror = () => reject(transaction.error);
            request.onerror = () => reject(request.error);
        });
    },

    /**
     * Get the most recent snapshot from IndexedDB
     */
    async getPreviousSnapshot() {
        const db = await this.open();
        const transaction = db.transaction([STORE_NAME], 'readonly');
        const store = transaction.objectStore(STORE_NAME);

        return new Promise((resolve, reject) => {
            // Get all snapshots and find the most recent one
            const request = store.getAll();

            request.onsuccess = () => {
                const snapshots = request.result;
                if (snapshots.length === 0) {
                    resolve(null);
                    return;
                }

                // Sort by timestamp descending and get the most recent
                const mostRecent = snapshots.sort((a, b) => b.timestamp - a.timestamp)[0];
                resolve(mostRecent);
            };

            request.onerror = () => reject(request.error);
        });
    },

    /**
     * Clean up old snapshots, keeping only the most recent one
     */
    async cleanupOldSnapshots() {
        const db = await this.open();
        const transaction = db.transaction([STORE_NAME], 'readwrite');
        const store = transaction.objectStore(STORE_NAME);

        return new Promise((resolve, reject) => {
            const request = store.getAll();

            request.onsuccess = () => {
                const snapshots = request.result;
                if (snapshots.length <= 1) {
                    resolve();
                    return;
                }

                // Sort by timestamp descending and delete all but the first
                snapshots.sort((a, b) => b.timestamp - a.timestamp);
                const toDelete = snapshots.slice(1);

                toDelete.forEach(snapshot => {
                    store.delete(snapshot.timestamp);
                });

                transaction.oncomplete = () => resolve();
                transaction.onerror = () => reject(transaction.error);
            };

            request.onerror = () => reject(request.error);
        });
    },

    /**
     * Clear all snapshots (for testing/debugging)
     */
    async clearAllSnapshots() {
        const db = await this.open();
        const transaction = db.transaction([STORE_NAME], 'readwrite');
        const store = transaction.objectStore(STORE_NAME);

        return new Promise((resolve, reject) => {
            const request = store.clear();
            request.onsuccess = () => resolve();
            request.onerror = () => reject(request.error);
        });
    }
};

/**
 * Compare current and previous snapshots to detect changes
 */
function compareSnapshots(currentListings, previousSnapshot) {
    if (!previousSnapshot || !previousSnapshot.listings) {
        return {
            new: [],
            removed: [],
            priceChanges: [],
            specChanges: [],
            hasPreviousData: false
        };
    }

    const previousListings = previousSnapshot.listings;

    // Build maps using config signatures
    const currentBySignature = new Map();
    const currentById = new Map();

    currentListings.forEach(listing => {
        const signature = generateConfigSignature(listing);
        currentBySignature.set(signature, listing);
        currentById.set(listing.id, listing);
    });

    const previousBySignature = new Map();
    const previousById = new Map();

    previousListings.forEach(listing => {
        const signature = generateConfigSignature(listing);
        previousBySignature.set(signature, listing);
        previousById.set(listing.id, listing);
    });

    // Find new listings (signatures in current but not in previous)
    const newSignatures = new Set();
    for (const [signature] of currentBySignature) {
        if (!previousBySignature.has(signature)) {
            newSignatures.add(signature);
        }
    }

    const newListings = Array.from(newSignatures).map(sig => currentBySignature.get(sig));

    // Find removed listings (signatures in previous but not in current)
    const removedSignatures = new Set();
    for (const [signature] of previousBySignature) {
        if (!currentBySignature.has(signature)) {
            removedSignatures.add(signature);
        }
    }

    const removedListings = Array.from(removedSignatures).map(sig => previousBySignature.get(sig));

    // Find price changes (same signature, different price)
    const priceChanges = [];
    for (const [signature, currentListing] of currentBySignature) {
        const previousListing = previousBySignature.get(signature);
        if (previousListing && currentListing.price_effective_monthly !== previousListing.price_effective_monthly) {
            priceChanges.push({
                signature,
                current: currentListing,
                previous: previousListing,
                oldPrice: previousListing.price_effective_monthly,
                newPrice: currentListing.price_effective_monthly,
                priceDifference: currentListing.price_effective_monthly - previousListing.price_effective_monthly,
                percentChange: ((currentListing.price_effective_monthly - previousListing.price_effective_monthly) / previousListing.price_effective_monthly) * 100
            });
        }
    }

    // Find spec changes (same ID but different specs)
    const specChanges = [];
    for (const [id, currentListing] of currentById) {
        const previousListing = previousById.get(id);
        if (previousListing) {
            const currentSig = generateConfigSignature(currentListing);
            const previousSig = generateConfigSignature(previousListing);

            if (currentSig !== previousSig) {
                specChanges.push({
                    id,
                    current: currentListing,
                    previous: previousListing,
                    oldSignature: previousSig,
                    newSignature: currentSig
                });
            }
        }
    }

    return {
        new: newListings,
        removed: removedListings,
        priceChanges: priceChanges.sort((a, b) => Math.abs(b.percentChange) - Math.abs(a.percentChange)),
        specChanges,
        hasPreviousData: true,
        previousTimestamp: previousSnapshot.timestamp
    };
}

/**
 * Main snapshot diff manager
 */
const snapshotDiffManager = {
    currentDiff: null,
    isInitialized: false,

    /**
     * Initialize the snapshot diff system
     */
    async initialize() {
        if (this.isInitialized) return;

        try {
            // Open IndexedDB to ensure it's ready
            await SnapshotDB.open();
            this.isInitialized = true;
            console.log('Snapshot diff system initialized');
        } catch (error) {
            console.error('Failed to initialize snapshot diff system:', error);
            throw error;
        }
    },

    /**
     * Process current listings and generate diff
     */
    async processCurrentListings(listings) {
        if (!this.isInitialized) {
            await this.initialize();
        }

        try {
            // Get previous snapshot
            const previousSnapshot = await SnapshotDB.getPreviousSnapshot();

            // Generate diff
            this.currentDiff = compareSnapshots(listings, previousSnapshot);

            // Save current snapshot for next time
            await SnapshotDB.saveSnapshot(listings);

            console.log('Snapshot diff generated:', {
                new: this.currentDiff.new.length,
                removed: this.currentDiff.removed.length,
                priceChanges: this.currentDiff.priceChanges.length,
                specChanges: this.currentDiff.specChanges.length,
                hasPreviousData: this.currentDiff.hasPreviousData
            });

            // Trigger UI update
            this.updateUI();

            return this.currentDiff;
        } catch (error) {
            console.error('Failed to process listings for diff:', error);
            throw error;
        }
    },

    /**
     * Update the UI with diff information
     */
    updateUI() {
        if (!this.currentDiff || !this.currentDiff.hasPreviousData) {
            // No previous data to compare against
            this.hideDiffSummary();
            return;
        }

        const hasChanges = this.currentDiff.new.length > 0
            || this.currentDiff.removed.length > 0
            || this.currentDiff.priceChanges.length > 0
            || this.currentDiff.specChanges.length > 0;
        const dismissedComparison = localStorage.getItem(DISMISSED_DIFF_KEY);
        if (!hasChanges || dismissedComparison === String(this.currentDiff.previousTimestamp)) {
            this.hideDiffSummary();
            return;
        }

        const summary = this.buildDiffSummary();
        this.showDiffSummary(summary);
    },

    /**
     * Build a human-readable diff summary
     */
    buildDiffSummary() {
        const { new: newListings, removed, priceChanges, specChanges } = this.currentDiff;

        const parts = [];

        if (newListings.length > 0) {
            parts.push(`${newListings.length} new`);
        }

        if (removed.length > 0) {
            parts.push(`${removed.length} removed`);
        }

        if (priceChanges.length > 0) {
            const priceDecreases = priceChanges.filter(c => c.priceDifference < 0);
            const priceIncreases = priceChanges.filter(c => c.priceDifference > 0);

            if (priceDecreases.length > 0) {
                parts.push(`${priceDecreases.length} price decrease${priceDecreases.length > 1 ? 's' : ''}`);
            }
            if (priceIncreases.length > 0) {
                parts.push(`${priceIncreases.length} price increase${priceIncreases.length > 1 ? 's' : ''}`);
            }
        }

        if (specChanges.length > 0) {
            parts.push(`${specChanges.length} spec change${specChanges.length > 1 ? 's' : ''}`);
        }

        return parts.length > 0 ? parts.join(', ') : 'No changes';
    },

    /**
     * Show diff summary in the UI
     */
    showDiffSummary(summary) {
        // Create or update diff summary element
        let summaryElement = document.getElementById('diff-summary');
        if (!summaryElement) {
            summaryElement = document.createElement('div');
            summaryElement.id = 'diff-summary';
            summaryElement.className = 'diff-summary';

            // Insert after staleness indicator
            const stalenessIndicator = document.getElementById('staleness-indicator');
            if (stalenessIndicator && stalenessIndicator.nextSibling) {
                stalenessIndicator.parentNode.insertBefore(summaryElement, stalenessIndicator.nextSibling);
            }
        }

        summaryElement.innerHTML = `
            <span><strong>Since you last looked:</strong> ${summary}</span>
            <span class="diff-summary-actions">
                <button class="diff-details-btn" onclick="snapshotDiffManager.showDiffDetails()">View details</button>
                <button class="diff-dismiss-btn" onclick="snapshotDiffManager.dismissDiffSummary()" aria-label="Dismiss changes summary" title="Hide until a newer comparison is available">×</button>
            </span>
        `;
    },

    dismissDiffSummary() {
        if (this.currentDiff?.previousTimestamp) {
            localStorage.setItem(DISMISSED_DIFF_KEY, String(this.currentDiff.previousTimestamp));
        }
        this.hideDiffSummary();
    },

    /**
     * Hide diff summary
     */
    hideDiffSummary() {
        const summaryElement = document.getElementById('diff-summary');
        if (summaryElement) {
            summaryElement.remove();
        }
    },

    /**
     * Show detailed diff information
     */
    showDiffDetails() {
        if (!this.currentDiff) {
            alert('No diff data available');
            return;
        }

        const { new: newListings, removed, priceChanges, specChanges } = this.currentDiff;

        let details = '=== Changes Since Your Last Visit ===\n\n';

        if (newListings.length > 0) {
            details += `--- NEW LISTINGS (${newListings.length}) ---\n`;
            newListings.forEach(listing => {
                details += `• ${listing.cpu_model}, ${listing.ram_gb}GB RAM - €${listing.price_effective_monthly.toFixed(2)}/month\n`;
            });
            details += '\n';
        }

        if (removed.length > 0) {
            details += `--- REMOVED LISTINGS (${removed.length}) ---\n`;
            removed.forEach(listing => {
                details += `• ${listing.cpu_model}, ${listing.ram_gb}GB RAM - was €${listing.price_effective_monthly.toFixed(2)}/month\n`;
            });
            details += '\n';
        }

        if (priceChanges.length > 0) {
            details += `--- PRICE CHANGES (${priceChanges.length}) ---\n`;
            priceChanges.forEach(change => {
                const direction = change.priceDifference < 0 ? '↓' : '↑';
                const color = change.priceDifference < 0 ? ' (better!)' : '';
                details += `• ${change.current.cpu_model}, ${change.ram_gb}GB RAM: €${change.oldPrice.toFixed(2)} → €${change.newPrice.toFixed(2)} ${direction}${color}\n`;
            });
            details += '\n';
        }

        if (specChanges.length > 0) {
            details += `--- SPEC CHANGES (${specChanges.length}) ---\n`;
            specChanges.forEach(change => {
                details += `• ID ${change.id}: Configuration changed\n`;
            });
            details += '\n';
        }

        if (newListings.length === 0 && removed.length === 0 && priceChanges.length === 0 && specChanges.length === 0) {
            details += 'No changes detected since your last visit.';
        }

        alert(details);
    },

    /**
     * Get current diff data
     */
    getCurrentDiff() {
        return this.currentDiff;
    },

    /**
     * Check if there are significant changes to highlight
     */
    hasSignificantChanges() {
        if (!this.currentDiff) return false;

        // Define "significant" as any price decreases or more than 5 new listings
        const significantPriceDrops = this.currentDiff.priceChanges.filter(c => c.priceDifference < -5);
        return significantPriceDrops.length > 0 || this.currentDiff.new.length > 5;
    },

    /**
     * Reset/clear all stored snapshots (for testing)
     */
    async reset() {
        await SnapshotDB.clearAllSnapshots();
        this.currentDiff = null;
        this.hideDiffSummary();
        console.log('Snapshot diff system reset');
    }
};

// Export for use in other scripts
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        generateConfigSignature,
        normalizeCpuModel,
        SnapshotDB,
        compareSnapshots,
        snapshotDiffManager
    };
}
