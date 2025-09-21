import csv, json, os
from typing import List, Dict, Any, Tuple # Typing helpers

# Read a CSV file and return list of dict rows
def read_csv(path: str) -> List[Dict[str, Any]]:
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)      # Reads CSV into dicts keyed by header row
        rows = [dict(r) for r in reader]
    return rows  # Each row is a {column: value} dict (all values as strings)

# Write list of dict rows to CSV
def write_csv(path: str, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        # If no data, create empty file (no header)
        open(path, "w", encoding="utf-8").close()
        return
    fieldnames = list(rows[0].keys())   # Use keys from first row as header
    with open(path, "w", newline='', encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)               # Write each row dict as a line

# Write a Python object to JSON file (pretty-printed, sorted keys)
def write_json(path: str, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True)

# Ensure a directory exists, create it if needed (idempotent)
def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)

