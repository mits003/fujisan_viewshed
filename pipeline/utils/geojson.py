"""Shared GeoJSON utilities."""


def features_to_dicts(features: list[dict]) -> list[dict]:
    """Convert GeoJSON features to flat dicts with lat/lon promoted from geometry."""
    results = []
    for f in features:
        props = f["properties"]
        lon, lat = f["geometry"]["coordinates"]
        results.append({**props, "lat": lat, "lon": lon})
    return results
