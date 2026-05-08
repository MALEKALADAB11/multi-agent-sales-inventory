"""
Store ID mapping - different modules use different store IDs
This is the ONLY place that knows about store ID differences
"""

# Mapping: module's store ID -> canonical store ID (from CSV)
STORE_MAPPING = {
    # Sales module uses these
    "store-lac2": "STORE-001",
    "OOR_LAC_01": "STORE-001",
    
    # Inventory module uses this
    "STORE-001": "STORE-001",
    
    # Frontend uses this
    "lac2": "STORE-001",
}

# Reverse mapping for when you need to go the other way
REVERSE_MAPPING = {
    "STORE-001": "lac2",  # For frontend responses
}

def to_canonical_store_id(store_id: str) -> str:
    """Convert any store ID format to canonical STORE-001 format"""
    return STORE_MAPPING.get(store_id, store_id)

def to_frontend_store_id(store_id: str) -> str:
    """Convert canonical store ID to frontend format"""
    return REVERSE_MAPPING.get(store_id, store_id)