# Gujarat Police Innovation Hackathon 2026 — Final Submission Summary
**Project:** EMERGENCY AI / Sentinel Vehicle & Incident Intelligence Engine  
**Target Environment:** Gujarat Police Command & Control (Trinetra / Sentinel CCTV Grid)  
**Status:** Verification Complete & Code Frozen  
**Date:** September 28, 2026  

---

## 1. Executive Summary & Problem Statement

In modern metropolitan and highway surveillance (such as Gandhinagar and Ahmedabad command operations), law enforcement requires automated, zero-latency situational awareness. When an incident occurs (SOS panic trigger, intrusion, brawl, or accident), police dispatchers must instantly identify which vehicles were present at the scene, reconstruct their cross-camera flight paths, detect impossible teleportation anomalies (plate spoofing or tracking discontinuities), and cross-reference plates against state terror and crime watchlists.

This submission augments the verified **EMERGENCY AI** platform with high-reliability vehicle intelligence, temporal OCR consensus, multi-camera kinematic journey reconstruction, and actionable Police SOP dispatch advisories.

---

## 2. Final System Architecture

```text
[Sentinel CCTV RTSP Streams] (TCP Transport, Resilient Reconnect, PTS Timing)
             │
             ├──► [Discontinuity Detector] (64-bin Grayscale Hist-Corr < 0.70 & Δpts < -500ms)
             │          │ (Triggers TrackKey Session Rotation)
             ▼          ▼
[AI Perception Engines] 
  ├── Stable Detectors: Fall, Fight, Fire, SOS, Face (Detectors Unmodified)
  └── Vehicle & ANPR: YOLO Plate Detector + EasyOCR Engine
             │
             ▼
[Temporal Plate Fusion Engine] (Weighted Sliding Window Voting + Indian Syntax Validation)
             │
             ▼
[SQLite Core Persistence (emergency_ai.db)]
  ├── Existing Tables (Untouched): incidents, cameras, camera_zones
  └── Intelligence Table: vehicle_sightings (pts_ms, track_key, camera_id, confidence)
             │
             ▼
[FastAPI Backend Services]
  ├── GET /health, /cameras, /alerts
  ├── GET /journey/{plate} (Haversine Distance + Kinematic Feasibility ≤ 150 km/h)
  └── GET /correlations/{incident_id} (Spatial Camera & Temporal Window ±2 min Filter)
             │
             ▼
[Operator Command Dashboard]
  ├── Live Incident Feed + Priority Watchlist Badges
  ├── Police SOP Dispatch Advisory Cards
  └── Cross-Camera Journey Tracker with Interactive Leaflet Map (Green/Red-Dashed Polylines)
```

---

## 3. Ten Implemented Capabilities & Verification Evidence

1. **Sightings Persistence (`anpr/sightings.py`):**  
   *Evidence:* SQLite table `vehicle_sightings` created with full schema (`id`, `camera_id`, `plate_number`, `confidence`, `timestamp`, `pts_ms`, `snapshot_path`, `track_key`); verified via live insertions in `test_task9_e2e_urllib.py`.
2. **Temporal Plate Fusion (`anpr/pipeline.py`):**  
   *Evidence:* `TemporalPlateFusion` correctly fused noisy multi-frame sequence (`['GJ01AB1234', 'GJ01AB123?', 'GJ01AB1234']`) to clean plate `GJ01AB1234` with 0.92 confidence, discarding artifact character.
3. **TrackKey Session Isolation (`anpr/pipeline.py`):**  
   *Evidence:* Tracks indexed by `camera_id/stream_session_id/track_id` isolate identical track IDs across stream restarts (`cam04/sess_a/42` != `cam04/sess_b/42`).
4. **Scene-Cut Hardening (`anpr/live_capture.py`):**  
   *Evidence:* 64-bin histogram Pearson correlation flagged visual cuts (correlation `-0.016` < `0.70`) and backward PTS jumps ($\Delta\text{pts} < -500\text{ ms}$), automatically rotating session IDs.
5. **Journey Kinematic Feasibility API (`backend/app/routes/journey.py`):**  
   *Evidence:* `GET /journey/{plate}` validated Case A ($1.502\text{ km}$ in $300\text{ s} = 18.0\text{ km/h} \rightarrow \text{feasible: True}$) and flagged Case B ($12.593\text{ km}$ in $10\text{ s} = 4533.5\text{ km/h} \rightarrow \text{feasible: False}$).
6. **Incident-Vehicle Correlation API (`backend/app/routes/journey.py`):**  
   *Evidence:* `GET /correlations/{incident_id}` for incident at `14:00:00` on `cam04` matched vehicles at `14:01:00` and `13:59:00` while strictly rejecting $+6\text{ min}$ out-of-window and other cameras.
7. **Dashboard Journey UI & Leaflet Mapping (`frontend/`):**  
   *Evidence:* Real-time Leaflet map rendering with camera pins, popup timelines, and color-coded trajectory lines (**Green** for feasible hops, **Red dashed** for implausible jumps).
8. **Watchlist Alert Wiring & SOP Dispatch Advice (`anpr/alert_bridge.py`, `frontend/app.js`):**  
   *Evidence:* Seeded watchlist hit on `GJ01RQ6420` triggered priority alert in `/alerts` feed with inline Police SOP guidance card: *"Alert nearest PCR van / patrol unit immediately."*
9. **Comprehensive End-to-End Testing (`scratch/test_task9_e2e_urllib.py`):**  
   *Evidence:* Full automated test suite passed against live Uvicorn server (`/health`, `/cameras`, `/alerts`, `/journey/{plate}`, `/correlations/{id}`, scene cut, fusion).
10. **Demo Rehearsal & Code Freeze (`scratch/rehearsal_demo.py`):**  
    *Evidence:* Full 6-phase operational scenario executed with 100% pass rate; repository placed under strict code freeze.

---

## 4. Sentinel CCTV Compliance Notes

- **RTSP-over-TCP Transport:** Hard-enforced in `anpr/config.py` via `OPENCV_FFMPEG_CAPTURE_OPTIONS = "rtsp_transport;tcp"` to eliminate UDP packet loss and corrupted macroblocks on busy municipal networks.
- **PTS Presentation Timestamping:** All temporal logic strictly samples `cv2.CAP_PROP_POS_MSEC`. Arrival-time heuristics are forbidden, ensuring robustness against variable network latency.
- **Exponential Reconnection Backoff:** Reconnection begins at $2.0\text{ s}$ with a multiplier of $2.0\times$ up to a $30.0\text{ s}$ ceiling, preventing thundering herd load on camera encoders.
- **Zero Footage Download Policy:** Video frames are ingested directly into memory buffers and discarded after stride processing; no raw stream footage is downloaded or permanently stored.

---

## 5. Six-Phase Demo Walkthrough Scenario

1. **Phase 1 (Incident Detected):** Panic trigger / SOS alert #105 logged at `cam04` (CH Road Market Gate) at `15:30:00`.
2. **Phase 2 (Vehicle Correlation):** Querying `/correlations/105` correlates vehicle `GJ01RQ6420` departing `cam04` at `15:31:00` ($+60\text{ s}$ delta).
3. **Phase 3 (Temporal Fusion):** 4 multi-frame OCR detections with occlusion noise resolve to clean plate `GJ01RQ6420` (confidence 0.945).
4. **Phase 4 (Cross-Camera Journey):** Vehicle traced across Gandhinagar grid (`cam04` $\rightarrow$ `cam14` $\rightarrow$ `cam22`); Leaflet map plots green feasible route ($23.2\text{ km/h}$ and $26.9\text{ km/h}$).
5. **Phase 5 (Watchlist Hit):** Matched against crime bulletin: `Terror watchlist entry` (Levenshtein distance 0).
6. **Phase 6 (Police SOP Response):** System generates immediate priority dispatch card advising patrol units to intercept along heading CH Road -> Ring Road North -> Highway Toll Plaza.
