import json
import re
import subprocess
from pathlib import Path


def test_contextual_history_selection_collapses_and_suppresses():
    html = (Path(__file__).parents[2] / "web" / "index.html").read_text(encoding="utf-8")
    match = re.search(
        r"function selectContextualListings\(sourceListings, status\) \{.*?\n        \}",
        html,
        re.DOTALL,
    )
    assert match
    listings = [
        {"id": "active-a", "config_signature": "a", "active": True, "price_effective_monthly": 50, "last_seen_at": "2026-08-19"},
        {"id": "old-a", "config_signature": "a", "active": False, "price_effective_monthly": 30, "last_seen_at": "2026-08-18"},
        {"id": "old-b-high", "config_signature": "b", "active": False, "price_effective_monthly": 45, "last_seen_at": "2026-08-17"},
        {"id": "old-b-low", "config_signature": "b", "active": False, "price_effective_monthly": 35, "last_seen_at": "2026-08-16"},
    ]
    script = f"{match.group(0)}; console.log(JSON.stringify(selectContextualListings({json.dumps(listings)}, 'context').map(x => x.id)));"
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == ["active-a", "old-b-low"]
