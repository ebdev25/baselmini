from typing import List # Typing helper for clarity

# Define the Basel-style rating ladder, from safest (AAA) down to NR (Not Rated)
RATING_ORDER: List[str] = ["AAA","AA","A","BBB","BB","B","CCC","CC","C","D","NR"]

# Normalise messy external ratings into one of the engine's canonical buckets
def normalize_rating(r: str) -> str:
    if not r:
        return "NR"  # If missing/empty, treat as Not Rated
    r = r.strip().upper()  # Clean whitespace and capitalise
    
    # Direct match against allowed rating set
    if r in {"AAA","AA","A","BBB","BB","B","CCC","CC","C","D"}:
        return r
    # Map common "not rated" forms to NR
    if r in {"NR","UNRATED","NA","N/A"}:
        return "NR"
    
    # Crude normalization rules:
    # Collapse variants with + / - back to their base bucket
    if r.startswith("AAA"): return "AAA"
    if r.startswith("AA"): return "AA"
    if r.startswith("A"): return "A"
    if r.startswith("BBB"): return "BBB"
    if r.startswith("BB"): return "BB"
    if r.startswith("B"): return "B"
    if r.startswith("CCC"): return "CCC"
    if r.startswith("CC"): return "CC"
    if r.startswith("C"): return "C"
    
    # Fallback if unrecognized: treat as Not Rated
    return "NR"

# Apply a rating downgrade ("notch down") by n steps on the rating ladder
def notch_down(r: str, n: int = 1) -> str:
    r = normalize_rating(r) # First normalise input rating
    try:
        idx = RATING_ORDER.index(r) # Find current position in rating ladder
    except ValueError:
        idx = len(RATING_ORDER) - 1 # If unknown, treat as NR at the bottom
    
    # Move down n notches, capped at the last element (NR)
    return RATING_ORDER[min(idx + max(0,n), len(RATING_ORDER) - 1)]

