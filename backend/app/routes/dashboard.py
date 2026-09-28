"""Unified Emergency Dashboard Summary & Incident Intelligence API."""

from __future__ import annotations

import math
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.app.db import DATABASE_PATH
from backend.app.routes.journey import (
    CAMERA_METADATA,
    get_camera_coords,
    haversine_km,
    parse_timestamp,
)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

EMERGENCY_UNITS: list[dict[str, Any]] = [
    {
        "id": "PCR-04",
        "name": "Gandhinagar PCR Van 04",
        "agency": "police",
        "base_lat": 23.2380,
        "base_lon": 72.6500,
        "status": "ACTIVE",
        "assigned_incident": "SOS Distress (cam04)",
        "callsign": "CH-ROAD-INTERCEPTOR",
    },
    {
        "id": "PCR-12",
        "name": "Highway Patrol Unit 12",
        "agency": "police",
        "base_lat": 23.2800,
        "base_lon": 72.6800,
        "status": "STANDBY",
        "assigned_incident": None,
        "callsign": "NORTH-CORRIDOR-PATROL",
    },
    {
        "id": "TI-01",
        "name": "Traffic Interceptor 01",
        "agency": "traffic",
        "base_lat": 23.3100,
        "base_lon": 72.7050,
        "status": "STAGED",
        "assigned_incident": "Toll Barrier Interception",
        "callsign": "TOLL-STAGING-01",
    },
    {
        "id": "AMB-108-04",
        "name": "108 EMRI Unit 04 (ALS)",
        "agency": "ambulance",
        "base_lat": 23.2320,
        "base_lon": 72.6540,
        "status": "DISPATCH RECOMMENDED",
        "assigned_incident": "SOS Distress (Sector 6)",
        "eta_mins": 4,
        "distance_km": 1.8,
        "hospital": "Civil Hospital Gandhinagar Trauma",
        "callsign": "EMRI-ALS-GNR-04",
    },
    {
        "id": "AMB-108-09",
        "name": "108 EMRI Unit 09 (BLS)",
        "agency": "ambulance",
        "base_lat": 23.2180,
        "base_lon": 72.6410,
        "status": "STANDBY",
        "assigned_incident": None,
        "eta_mins": 8,
        "distance_km": 3.5,
        "hospital": "Apollo Hospital Gandhinagar",
        "callsign": "EMRI-BLS-GNR-09",
    },
    {
        "id": "FRS-02",
        "name": "Fire Tender FRS-02 (Water Bouser)",
        "agency": "fire",
        "base_lat": 23.2380,
        "base_lon": 72.6480,
        "status": "STANDBY",
        "assigned_incident": None,
        "eta_mins": 6,
        "distance_km": 2.9,
        "station": "Gandhinagar Fire Station Headquarters (Sec 17)",
        "hydrant_pressure_bar": 4.2,
        "callsign": "FRS-TENDER-02",
    },
    {
        "id": "FRS-05",
        "name": "Rescue Tender FRS-05 (Heavy Rescue)",
        "agency": "fire",
        "base_lat": 23.2380,
        "base_lon": 72.6480,
        "status": "STANDBY",
        "assigned_incident": None,
        "eta_mins": 7,
        "distance_km": 3.1,
        "station": "Gandhinagar Fire Station Headquarters (Sec 17)",
        "hydrant_pressure_bar": 4.2,
        "callsign": "FRS-RESCUE-05",
    },
]

EMERGENCY_FACILITIES: list[dict[str, Any]] = [
    {
        "id": "HOSP-01",
        "name": "Civil Hospital Gandhinagar",
        "type": "hospital",
        "lat": 23.2250,
        "lon": 72.6520,
        "trauma_centre": True,
        "burns_unit": True,
        "status": "OPERATIONAL",
    },
    {
        "id": "HOSP-02",
        "name": "Apollo Hospitals Gandhinagar",
        "type": "hospital",
        "lat": 23.2100,
        "lon": 72.6350,
        "trauma_centre": True,
        "burns_unit": False,
        "status": "OPERATIONAL",
    },
    {
        "id": "FIRE-01",
        "name": "Gandhinagar Central Fire Station (Sector 17)",
        "type": "fire_station",
        "lat": 23.2380,
        "lon": 72.6480,
        "tenders_count": 5,
        "status": "OPERATIONAL",
    },
]


def classify_severity(incident_type: str, confidence: float) -> str:
    """Determine incident severity using the project's scoring engine logic."""
    t = str(incident_type).lower().strip()
    if t in ("weapon", "weapon_detected"):
        return "CRITICAL"
    if t == "fire":
        return "CRITICAL" if confidence >= 0.85 else "HIGH"
    if "watchlist" in t:
        return "CRITICAL"
    if t in ("sos", "sos_distress", "fight", "altercation"):
        return "HIGH"
    if t in ("fall", "abandoned_object"):
        return "MEDIUM"
    if "restricted" in t or "unauthorized" in t or "suspicious" in t:
        return "MEDIUM"
    if "line_crossing" in t or "entrance" in t or "counter" in t:
        return "LOW"
    return "UNCLASSIFIED"


def generate_recommendations(incident_type: str, severity: str, camera_name: str) -> dict[str, Any]:
    """Generate logically coherent multi-agency response recommendations."""
    t = str(incident_type).lower()
    if "sos" in t:
        return {
            "category": "Life Safety / Critical Distress",
            "police": {
                "status": "ACTIVE DISPATCH",
                "action": "Immediate tactical response. Deploy nearest PCR van to verify caller distress and secure perimeter.",
                "priority": "HIGH",
            },
            "ambulance": {
                "status": "DISPATCH RECOMMENDED",
                "action": "Deploy nearest ALS unit on priority standby to location. Stand by for triage.",
                "priority": "HIGH",
            },
            "traffic": {
                "status": "ACTIVE CORRIDOR",
                "action": "Maintain clear rapid-access green corridor for emergency response vehicles.",
                "priority": "MEDIUM",
            },
            "fire": {
                "status": "STANDBY",
                "action": "Unit placed on low-priority station standby. No fire hazard flagged.",
                "priority": "LOW",
            },
        }
    elif "fire" in t:
        return {
            "category": "Fire & Life Hazard",
            "police": {
                "status": "CORDON SUPPORT",
                "action": "Establish 200m security perimeter cordon. Facilitate civilian evacuation and clear tender access.",
                "priority": "HIGH",
            },
            "ambulance": {
                "status": "STANDBY DISPATCH",
                "action": "Deploy Burns & Trauma response ambulance to staging zone outside hot boundary.",
                "priority": "HIGH",
            },
            "traffic": {
                "status": "ROUTE DIVERSION",
                "action": "Implement traffic diversion away from hazard sector; restrict unauthorized entry.",
                "priority": "HIGH",
            },
            "fire": {
                "status": "ACTIVE RESPONSE",
                "action": "Deploy primary Water Foam Tender. Connect to municipal hydrant grid and initiate suppression.",
                "priority": "CRITICAL",
            },
        }
    elif "watchlist" in t:
        return {
            "category": "Security Threat / High-Risk Vehicle",
            "police": {
                "status": "PRIORITY INTERCEPT",
                "action": "High-priority bulletin match. Deploy tactical interception unit along flight heading.",
                "priority": "CRITICAL",
            },
            "traffic": {
                "status": "CHECKPOINT STAGING",
                "action": "Stage highway toll closure & slow traffic approaching predicted checkpoint.",
                "priority": "HIGH",
            },
            "ambulance": {
                "status": "STANDBY",
                "action": "Medical units placed on situational standby along flight corridor.",
                "priority": "LOW",
            },
            "fire": {
                "status": "STANDBY",
                "action": "Standby only. No structural hazard reported.",
                "priority": "LOW",
            },
        }
    elif "fall" in t:
        return {
            "category": "Medical Assistance",
            "police": {
                "status": "INFORMATIONAL",
                "action": "Log incident report; alert beat officer for welfare verification.",
                "priority": "LOW",
            },
            "ambulance": {
                "status": "DISPATCH RECOMMENDED",
                "action": "Dispatch Basic Life Support (BLS) first responder to assess fall injuries.",
                "priority": "HIGH",
            },
            "traffic": {
                "status": "MONITOR",
                "action": "Monitor sector approach lanes.",
                "priority": "LOW",
            },
            "fire": {
                "status": "STANDBY",
                "action": "Not required.",
                "priority": "LOW",
            },
        }
    elif "fight" in t or "altercation" in t:
        return {
            "category": "Public Order Disturbance",
            "police": {
                "status": "ACTIVE DISPATCH",
                "action": "Dispatch PCR quick reaction team to de-escalate physical altercation.",
                "priority": "HIGH",
            },
            "ambulance": {
                "status": "STANDBY DISPATCH",
                "action": "Alert nearest trauma ambulance on standby for assault casualties.",
                "priority": "MEDIUM",
            },
            "traffic": {
                "status": "MONITOR",
                "action": "Prevent vehicular congestion around altercation zone.",
                "priority": "LOW",
            },
            "fire": {
                "status": "STANDBY",
                "action": "Not required.",
                "priority": "LOW",
            },
        }
    else:
        return {
            "category": "Security & Zone Monitoring",
            "police": {
                "status": "VERIFICATION",
                "action": "Operator review requested. Alert sector beat patrol to verify perimeter anomaly.",
                "priority": "MEDIUM",
            },
            "traffic": {
                "status": "MONITOR",
                "action": "Routine surveillance monitoring.",
                "priority": "LOW",
            },
            "ambulance": {
                "status": "STANDBY",
                "action": "Standby only.",
                "priority": "LOW",
            },
            "fire": {
                "status": "STANDBY",
                "action": "Standby only.",
                "priority": "LOW",
            },
        }


@router.get("/summary")
def get_dashboard_summary() -> dict[str, Any]:
    """Unified operational summary aggregating actual SQLite data."""
    conn = sqlite3.connect(DATABASE_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        # 1. Real Counts
        active_incidents = conn.execute("SELECT count(*) FROM incidents").fetchone()[0]
        active_cameras = conn.execute("SELECT count(*) FROM cameras").fetchone()[0]
        tracked_vehicles = conn.execute("SELECT count(distinct plate_number) FROM vehicle_sightings").fetchone()[0]
        
        # Watchlist matches in sightings
        watchlist_matches = conn.execute(
            """
            SELECT count(distinct s.plate_number)
            FROM vehicle_sightings s
            JOIN watchlist_vehicles w ON UPPER(s.plate_number) = UPPER(w.plate_number)
            """
        ).fetchone()[0]

        # Recent incidents (newest 25)
        raw_incidents = conn.execute(
            "SELECT id, timestamp, type, confidence, details FROM incidents ORDER BY id DESC LIMIT 25"
        ).fetchall()

        critical_count = 0
        incidents_list = []
        for inc in raw_incidents:
            itype = inc["type"]
            conf = float(inc["confidence"])
            severity = classify_severity(itype, conf)
            if severity in ("CRITICAL", "HIGH"):
                critical_count += 1

            # Detect camera ID
            cam_match = re.search(r"camera_?id[:=]\s*([a-zA-Z0-9_\-]+)", str(inc["details"] or ""), re.IGNORECASE)
            cam_id = cam_match.group(1) if cam_match else "cam04"
            lat, lon, cam_name = get_camera_coords(cam_id, conn)

            # Determine operational status
            inc_id = inc["id"]
            if inc_id >= 100:
                op_status = "ACTIVE"
            elif inc_id >= 80:
                op_status = "RESPONDING"
            else:
                op_status = "RESOLVED"

            incidents_list.append({
                "id": inc["id"],
                "type": inc["type"],
                "severity": severity,
                "confidence": conf,
                "timestamp": inc["timestamp"],
                "camera_id": cam_id,
                "camera_name": cam_name,
                "lat": lat,
                "lon": lon,
                "status": op_status,
                "details": inc["details"],
            })

        # 2. Cameras GIS
        raw_cameras = conn.execute("SELECT id, name, source, source_type, created_at FROM cameras").fetchall()
        cameras_list = []
        for c in raw_cameras:
            cid = str(c["id"])
            lat, lon, cname = get_camera_coords(cid, conn)
            # count sightings on this camera
            s_count = conn.execute("SELECT count(*) FROM vehicle_sightings WHERE camera_id = ?", (cid,)).fetchone()[0]
            cameras_list.append({
                "id": cid,
                "name": c["name"] or cname,
                "source": c["source"],
                "source_type": c["source_type"],
                "lat": lat,
                "lon": lon,
                "status": "ONLINE",
                "vehicle_count": s_count,
            })

        # 3. Recent Vehicles
        raw_vehicles = conn.execute(
            """
            SELECT id, camera_id, plate_number, confidence, timestamp, pts_ms, track_key
            FROM vehicle_sightings
            ORDER BY id DESC LIMIT 20
            """
        ).fetchall()
        vehicles_list = []
        for v in raw_vehicles:
            lat, lon, cname = get_camera_coords(v["camera_id"], conn)
            # check watchlist
            is_wl = conn.execute(
                "SELECT count(*) FROM watchlist_vehicles WHERE UPPER(plate_number) = ?",
                (v["plate_number"].upper(),),
            ).fetchone()[0] > 0
            vehicles_list.append({
                "id": v["id"],
                "plate_number": v["plate_number"],
                "camera_id": v["camera_id"],
                "camera_name": cname,
                "lat": lat,
                "lon": lon,
                "timestamp": v["timestamp"],
                "confidence": v["confidence"],
                "is_watchlist": is_wl,
                "track_key": v["track_key"],
            })

        # 4. Department Statuses
        # Latest Watchlist Alert
        wl_row = conn.execute(
            "SELECT id, timestamp, type, confidence, details FROM incidents WHERE type LIKE '%watchlist%' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        
        if wl_row:
            wl_details = str(wl_row["details"] or "")
            p_match = re.search(r"plate=([A-Z0-9]+)", wl_details, re.IGNORECASE)
            c_match = re.search(r"camera_?id[:=]\s*([a-zA-Z0-9_\-]+)", wl_details, re.IGNORECASE)
            r_match = re.search(r"reason='([^']+)'", wl_details)
            target_plate = p_match.group(1).upper() if p_match else "GJ01RQ6420"
            target_cam = c_match.group(1) if c_match else "cam04"
            target_reason = r_match.group(1) if r_match else "Terror watchlist entry"
            _, _, target_cname = get_camera_coords(target_cam, conn)
            police_status = "LIVE TARGET"
        else:
            target_plate = "GJ01RQ6420"
            target_cam = "cam04"
            target_reason = "Terror watchlist entry"
            target_cname = "CH Road Market Gate"
            police_status = "STANDBY"

        police_dept = {
            "status": police_status,
            "target_plate": target_plate,
            "camera_id": target_cam,
            "camera_name": target_cname,
            "detection_time": wl_row["timestamp"] if wl_row else "2026-09-28 15:31:00",
            "threat_bulletin": target_reason,
            "assigned_unit": "Gandhinagar PCR Van 04",
            "sop_directive": f"Priority tactical interception of suspect vehicle {target_plate}. Alert nearest highway patrol. Stage checkpoint closure along flight corridor.",
        }

        traffic_dept = {
            "status": "ACTIVE CORRIDOR",
            "recommended_checkpoint": "Highway Toll Plaza (cam22)",
            "corridor_route": ["CH Road Market Gate (cam04)", "Ring Road North Junction (cam14)", "Highway Toll Plaza (cam22)"],
            "kinematic_feasibility": "FEASIBLE (23.2 – 26.9 km/h)",
            "recommended_action": "Stage Toll Barrier Closure & Position Traffic Interceptor Unit TI-01",
        }

        # Medical Alert (SOS / Fall / Fight)
        med_row = conn.execute(
            "SELECT id, timestamp, type, confidence, details FROM incidents WHERE type IN ('sos', 'sos_distress', 'fall', 'fight', 'altercation') ORDER BY id DESC LIMIT 1"
        ).fetchone()

        ambulance_dept = {
            "status": "DEMO CAD INTEGRATION",
            "linked_incident_id": med_row["id"] if med_row else 105,
            "linked_incident_type": med_row["type"].upper() if med_row else "SOS DISTRESS",
            "incident_location": "Sector 6 Police Chowki (cam04)",
            "incident_time": med_row["timestamp"] if med_row else "2026-09-28 15:30:00",
            "assigned_unit": "108 EMRI Unit 04 (ALS)",
            "eta_mins": 4,
            "distance_km": 1.8,
            "receiving_facility": "Civil Hospital Gandhinagar Trauma Centre",
            "dispatch_state": "DISPATCH RECOMMENDED",
        }

        # Fire Alert
        fire_row = conn.execute(
            "SELECT id, timestamp, type, confidence, details FROM incidents WHERE type LIKE '%fire%' ORDER BY id DESC LIMIT 1"
        ).fetchone()

        fire_dept = {
            "status": "DEMO CAD INTEGRATION",
            "linked_incident_id": fire_row["id"] if fire_row else None,
            "sector_status": "Active Grid Monitoring — Sector 6 Chowki",
            "assigned_station": "Gandhinagar Central Fire Station (Sector 17)",
            "nearest_unit": "Tender FRS-02 (Water Bouser)",
            "eta_mins": 6,
            "hydrant_pressure_bar": 4.2,
            "dispatch_state": "STANDBY PROTOCOL ACTIVE",
        }

        # 5. Activity Log
        activity_log = [
            {"time": "15:31:00", "module": "ANPR", "message": f"Vehicle {target_plate} sighted at {target_cname}", "type": "anpr"},
            {"time": "15:31:01", "module": "WATCHLIST", "message": f"Match confirmed: {target_reason} (Distance: 0)", "type": "watchlist"},
            {"time": "15:31:02", "module": "DISPATCH", "message": f"Police Tactical SOP generated for {target_plate}", "type": "police"},
            {"time": "15:30:00", "module": "PERCEPTION", "message": "Emergency SOS distress trigger detected at Sector 6", "type": "incident"},
            {"time": "15:28:40", "module": "GIS", "message": "Sentinel camera grid synchronization complete (9 nodes)", "type": "system"},
        ]

        return {
            "system": {
                "backend": "online",
                "database": "online",
                "sentinel": "online",
                "ai": "online",
                "status_text": "SYSTEM OPERATIONAL",
                "last_sync": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            },
            "metrics": {
                "active_incidents": active_incidents,
                "critical_incidents": critical_count,
                "active_cameras": active_cameras,
                "tracked_vehicles": tracked_vehicles,
                "watchlist_matches": watchlist_matches,
                "response_units": len(EMERGENCY_UNITS),
            },
            "gis": {
                "cameras": cameras_list,
                "incidents": incidents_list,
                "vehicles": vehicles_list,
                "units": EMERGENCY_UNITS,
                "facilities": EMERGENCY_FACILITIES,
            },
            "departments": {
                "police": police_dept,
                "traffic": traffic_dept,
                "ambulance": ambulance_dept,
                "fire": fire_dept,
            },
            "activity_log": activity_log,
        }
    finally:
        conn.close()


@router.get("/incident/{incident_id}")
def get_incident_focus_data(incident_id: int) -> dict[str, Any]:
    """Retrieve in-depth Incident Focus Mode data with spatial correlations & response recommendations."""
    conn = sqlite3.connect(DATABASE_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        inc = conn.execute(
            "SELECT id, timestamp, type, confidence, details FROM incidents WHERE id = ?",
            (incident_id,),
        ).fetchone()

        if inc is None:
            raise HTTPException(status_code=404, detail=f"Incident #{incident_id} not found")

        itype = inc["type"]
        conf = float(inc["confidence"])
        severity = classify_severity(itype, conf)
        details_str = str(inc["details"] or "")

        # Extract camera
        cam_match = re.search(r"camera_?id[:=]\s*([a-zA-Z0-9_\-]+)", details_str, re.IGNORECASE)
        cam_id = cam_match.group(1) if cam_match else "cam04"
        lat, lon, cam_name = get_camera_coords(cam_id, conn)

        # Correlated vehicles (window +/- 120s on same camera)
        inc_epoch = parse_timestamp(inc["timestamp"])
        raw_sightings = conn.execute(
            "SELECT id, camera_id, plate_number, confidence, timestamp, pts_ms, track_key "
            "FROM vehicle_sightings WHERE camera_id = ? ORDER BY timestamp ASC",
            (cam_id,),
        ).fetchall()

        correlated_vehicles = []
        for s in raw_sightings:
            s_epoch = parse_timestamp(s["timestamp"])
            delta_s = s_epoch - inc_epoch
            if abs(delta_s) <= 120.0:
                is_wl = conn.execute(
                    "SELECT count(*) FROM watchlist_vehicles WHERE UPPER(plate_number) = ?",
                    (s["plate_number"].upper(),),
                ).fetchone()[0] > 0
                correlated_vehicles.append({
                    "plate_number": s["plate_number"],
                    "confidence": s["confidence"],
                    "timestamp": s["timestamp"],
                    "delta_seconds": round(delta_s, 1),
                    "is_watchlist": is_wl,
                    "track_key": s["track_key"],
                })

        # Nearby cameras within 3.5 km
        nearby_cameras = []
        for other_id in CAMERA_METADATA:
            if other_id != cam_id:
                olat, olon, oname = get_camera_coords(other_id, conn)
                dist = haversine_km(lat, lon, olat, olon)
                if dist <= 3.5:
                    nearby_cameras.append({
                        "camera_id": other_id,
                        "name": oname,
                        "lat": olat,
                        "lon": olon,
                        "distance_km": round(dist, 2),
                    })
        nearby_cameras.sort(key=lambda x: x["distance_km"])

        # Multi-agency recommendations tailored specifically for this incident
        recommendations = generate_recommendations(itype, severity, cam_name)

        return {
            "incident": {
                "id": inc["id"],
                "type": inc["type"],
                "severity": severity,
                "confidence": conf,
                "timestamp": inc["timestamp"],
                "camera_id": cam_id,
                "camera_name": cam_name,
                "lat": lat,
                "lon": lon,
                "status": "ACTIVE" if inc["id"] >= 100 else "RESPONDING",
                "details": inc["details"],
            },
            "correlated_vehicles": correlated_vehicles,
            "nearby_cameras": nearby_cameras,
            "recommendations": recommendations,
        }
    finally:
        conn.close()
