"""Vehicle Journey Reconstruction & Incident Correlation API."""

from __future__ import annotations

import math
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from anpr.config import DATABASE_PATH
from anpr.sightings import ensure_sightings_table, get_sightings_by_plate

router = APIRouter(prefix="", tags=["vehicle_intelligence"])

# Default coordinates for Sentinel CCTV grid cameras (Gandhinagar / Ahmedabad)
CAMERA_METADATA: dict[str, dict[str, Any]] = {
    "cam01": {"lat": 23.2156, "lon": 72.6369, "name": "GH-0 Circle, Gandhinagar"},
    "cam02": {"lat": 23.2230, "lon": 72.6492, "name": "CH-0 Circle, Gandhinagar"},
    "cam03": {"lat": 23.2312, "lon": 72.6580, "name": "Sector 6 Police Chowki"},
    "cam04": {"lat": 23.2420, "lon": 72.6510, "name": "CH Road Market Gate"},
    "cam14": {"lat": 23.2650, "lon": 72.6680, "name": "Ring Road North Junction"},
    "cam22": {"lat": 23.3100, "lon": 72.7050, "name": "Highway Toll Plaza"},
    "cam_arch": {"lat": 23.2180, "lon": 72.6400, "name": "Ancient Archway Gate"},
    "cam_test": {"lat": 23.2156, "lon": 72.6369, "name": "Test Intersection"},
    "1": {"lat": 23.2156, "lon": 72.6369, "name": "Campfire CCTV"},
    "2": {"lat": 23.2230, "lon": 72.6492, "name": "Sector 2 West Gate"},
    "3": {"lat": 23.2312, "lon": 72.6580, "name": "Sector 3 Chowk"},
    "4": {"lat": 23.2420, "lon": 72.6510, "name": "Marble Doorway Fixture"},
    "10": {"lat": 23.2200, "lon": 72.6450, "name": "Command Post Webcam"},
    "13": {"lat": 23.2250, "lon": 72.6500, "name": "Mobile IP Stream"},
}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great circle distance between two points in kilometers."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c


def get_camera_coords(camera_id: str, db: sqlite3.Connection) -> tuple[float, float, str]:
    """Retrieve camera latitude, longitude, and name, falling back to static grid metadata."""
    cid = str(camera_id)
    # Check if DB cameras table has lat/lon columns
    try:
        row = db.execute("SELECT name FROM cameras WHERE id = ? OR name = ?", (cid, cid)).fetchone()
        db_name = row["name"] if row else f"Camera {cid}"
    except Exception:
        db_name = f"Camera {cid}"

    if cid in CAMERA_METADATA:
        meta = CAMERA_METADATA[cid]
        return meta["lat"], meta["lon"], meta.get("name", db_name)

    # Deterministic default based on camera ID hash to spread across Gandhinagar area
    offset = (abs(hash(cid)) % 100) * 0.001
    return round(23.2150 + offset, 4), round(72.6350 + offset, 4), db_name


def parse_timestamp(ts: str) -> float:
    """Parse timestamp string into epoch seconds."""
    ts_str = str(ts).replace("T", " ").replace("Z", "").strip()
    if "." in ts_str:
        ts_str = ts_str.split(".")[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(ts_str, fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return 0.0


@router.get("/journey/{plate}")
def get_vehicle_journey(plate: str):
    """Reconstruct cross-camera vehicle journey for a license plate."""
    ensure_sightings_table()
    norm_plate = "".join(ch for ch in plate.upper() if ch.isalnum())
    if not norm_plate:
        raise HTTPException(status_code=400, detail="Invalid plate number")

    conn = sqlite3.connect(DATABASE_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        raw_sightings = get_sightings_by_plate(norm_plate, db_path=DATABASE_PATH)
    finally:
        conn.close()

    if not raw_sightings:
        return {
            "plate": norm_plate,
            "disclaimer": "Observed movement sequence. Not a confirmed route.",
            "sighting_count": 0,
            "sightings": [],
            "segments": [],
            "overall_feasible": True,
            "message": f"No sightings recorded for plate {norm_plate}",
        }

    # Enhance sightings with coordinates
    conn = sqlite3.connect(DATABASE_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    enhanced_sightings = []
    try:
        for s in raw_sightings:
            lat, lon, cam_name = get_camera_coords(s["camera_id"], conn)
            enhanced_sightings.append({
                "id": s["id"],
                "camera_id": s["camera_id"],
                "camera_name": cam_name,
                "lat": lat,
                "lon": lon,
                "plate_number": s["plate_number"],
                "confidence": s["confidence"],
                "timestamp": s["timestamp"],
                "pts_ms": s["pts_ms"],
                "snapshot_path": s["snapshot_path"],
                "track_key": s["track_key"],
            })
    finally:
        conn.close()

    # Reconstruct journey segments between consecutive sightings
    segments = []
    for i in range(len(enhanced_sightings) - 1):
        s1 = enhanced_sightings[i]
        s2 = enhanced_sightings[i + 1]

        t1 = parse_timestamp(s1["timestamp"])
        t2 = parse_timestamp(s2["timestamp"])
        elapsed_seconds = max(1.0, t2 - t1)

        dist_km = round(haversine_km(s1["lat"], s1["lon"], s2["lat"], s2["lon"]), 3)
        # If camera is identical, distance is 0.0
        if s1["camera_id"] == s2["camera_id"]:
            dist_km = 0.0

        required_speed_kmh = round((dist_km / (elapsed_seconds / 3600.0)), 1)
        feasible = required_speed_kmh <= 150.0

        segments.append({
            "segment_index": i + 1,
            "from_camera_id": s1["camera_id"],
            "to_camera_id": s2["camera_id"],
            "from_camera_name": s1["camera_name"],
            "to_camera_name": s2["camera_name"],
            "from_time": s1["timestamp"],
            "to_time": s2["timestamp"],
            "elapsed_seconds": int(elapsed_seconds),
            "distance_km": dist_km,
            "required_speed_kmh": required_speed_kmh,
            "feasible": feasible,
            "note": None if feasible else f"required speed {required_speed_kmh} km/h exceeds plausibility ceiling (150 km/h)",
        })

    overall_feasible = all(seg["feasible"] for seg in segments) if segments else True

    return {
        "plate": norm_plate,
        "disclaimer": "Observed movement sequence. Not a confirmed route.",
        "sighting_count": len(enhanced_sightings),
        "sightings": enhanced_sightings,
        "segments": segments,
        "overall_feasible": overall_feasible,
    }


@router.get("/correlations/{incident_id}")
def get_incident_correlations(
    incident_id: int,
    camera_id: str | None = Query(None, description="Filter by camera ID if known"),
    window_seconds: int = Query(120, description="Time window (+/- seconds) around incident"),
):
    """Find vehicle sightings around an incident camera within +/- window_seconds (default 2 min)."""
    ensure_sightings_table()
    conn = sqlite3.connect(DATABASE_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row

    try:
        incident_row = conn.execute(
            "SELECT id, timestamp, type, confidence, details FROM incidents WHERE id = ?",
            (incident_id,),
        ).fetchone()

        if incident_row is None:
            raise HTTPException(status_code=404, detail=f"Incident #{incident_id} not found")

        inc_time_str = incident_row["timestamp"]
        camera_id = None if hasattr(camera_id, "default") else camera_id
        window_seconds = 120 if hasattr(window_seconds, "default") else window_seconds
        inc_epoch = parse_timestamp(inc_time_str)

        # Detect camera from query, or details field
        target_camera = camera_id
        if not target_camera:
            details_str = str(incident_row["details"] or "")
            match = re.search(r"camera_?id[:=]\s*['\"]?([a-zA-Z0-9_\-]+)", details_str, re.IGNORECASE)
            if match:
                target_camera = match.group(1)

        # Fetch sightings
        if target_camera:
            sightings = conn.execute(
                "SELECT id, camera_id, plate_number, confidence, timestamp, pts_ms, snapshot_path, track_key "
                "FROM vehicle_sightings WHERE camera_id = ? ORDER BY timestamp ASC",
                (str(target_camera),),
            ).fetchall()
        else:
            sightings = conn.execute(
                "SELECT id, camera_id, plate_number, confidence, timestamp, pts_ms, snapshot_path, track_key "
                "FROM vehicle_sightings ORDER BY timestamp ASC"
            ).fetchall()

        correlated = []
        for s in sightings:
            s_epoch = parse_timestamp(s["timestamp"])
            delta_s = s_epoch - inc_epoch
            if abs(delta_s) <= window_seconds:
                correlated.append({
                    "id": s["id"],
                    "camera_id": s["camera_id"],
                    "plate_number": s["plate_number"],
                    "confidence": s["confidence"],
                    "timestamp": s["timestamp"],
                    "pts_ms": s["pts_ms"],
                    "track_key": s["track_key"],
                    "time_delta_seconds": round(delta_s, 1),
                })

        return {
            "incident_id": incident_row["id"],
            "incident_type": incident_row["type"],
            "incident_confidence": incident_row["confidence"],
            "incident_timestamp": inc_time_str,
            "camera_id": target_camera,
            "window_seconds": window_seconds,
            "correlated_sightings_count": len(correlated),
            "correlated_sightings": correlated,
        }
    finally:
        conn.close()