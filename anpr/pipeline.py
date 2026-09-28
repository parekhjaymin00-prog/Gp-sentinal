"""Live & Batch ANPR pipeline: ingest cameras -> RTSP / Video -> plate OCR -> fusion -> sightings -> watchlist."""

from __future__ import annotations

import os
import re
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from anpr.alert_bridge import emit_watchlist_alert
from anpr.config import ANPR_FRAME_STRIDE
from anpr.plate_detector import PlateDetection, PlateRecognizer
from anpr.sightings import ensure_sightings_table, record_sighting
from anpr.watchlist import find_watchlist_match, normalize_plate

INDIAN_PLATE_GRAMMAR = re.compile(r"^[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{4}$")
GRAMMAR_CONFIDENCE_PENALTY = 0.85
MIN_FUSION_WEIGHT = 0.10
MIN_FUSION_CONFIDENCE = 0.35


def grammar_ok(normalized: str) -> bool:
    """Check if normalized plate conforms to standard Indian registration format."""
    return bool(normalized) and INDIAN_PLATE_GRAMMAR.match(normalized) is not None


@dataclass(frozen=True)
class TrackKey:
    """Session-isolated tracking key: (camera_id, stream_session_id, track_id)."""

    camera_id: str
    stream_session_id: str
    track_id: int

    def __str__(self) -> str:
        return f"{self.camera_id}/{self.stream_session_id}/{self.track_id}"


@dataclass
class PlateObservation:
    text: str
    ocr_confidence: float
    image_quality: float
    frame_idx: int
    pts_ms: float


class TemporalPlateFusion:
    """Multi-frame temporal plate consensus across consecutive observations."""

    def __init__(
        self,
        window_size: int = 5,
        min_confidence: float = MIN_FUSION_CONFIDENCE,
        min_weight: float = MIN_FUSION_WEIGHT,
    ) -> None:
        self.window_size = window_size
        self.min_confidence = min_confidence
        self.min_weight = min_weight
        self.tracks: dict[str, list[PlateObservation]] = defaultdict(list)
        self.emitted_plates: dict[str, str] = {}

    def add_observation(
        self,
        track_key: str,
        text: str,
        ocr_confidence: float,
        image_quality: float,
        frame_idx: int,
        pts_ms: float,
    ) -> dict[str, Any] | None:
        norm = normalize_plate(text)
        if not norm or len(norm) < 4:
            return None

        obs = PlateObservation(
            text=norm,
            ocr_confidence=ocr_confidence,
            image_quality=image_quality,
            frame_idx=frame_idx,
            pts_ms=pts_ms,
        )
        self.tracks[track_key].append(obs)
        if len(self.tracks[track_key]) > self.window_size:
            self.tracks[track_key].pop(0)

        # Weighted voting consensus
        score: dict[str, float] = defaultdict(float)
        count: dict[str, int] = defaultdict(int)
        for o in self.tracks[track_key]:
            w = o.ocr_confidence * o.image_quality
            if w >= self.min_weight:
                score[o.text] += w
                count[o.text] += 1

        if not score:
            return None

        total_weight = sum(score.values())
        best_plate = max(score, key=score.get)
        confidence = score[best_plate] / total_weight

        # Soft grammar downgrade
        if not grammar_ok(best_plate):
            confidence *= GRAMMAR_CONFIDENCE_PENALTY

        if confidence < self.min_confidence:
            return None

        return {
            "plate_number": best_plate,
            "confidence": round(float(confidence), 3),
            "evidence_count": count[best_plate],
            "track_key": track_key,
        }

    def should_emit(self, track_key: str, plate_number: str) -> bool:
        """Avoid emitting duplicate sighting rows for the same vehicle in a stable track."""
        if self.emitted_plates.get(track_key) == plate_number:
            return False
        self.emitted_plates[track_key] = plate_number
        return True

    def reset_session(self, stream_session_id: str) -> None:
        """Clear tracks for an old session."""
        dead_keys = [k for k in self.tracks if stream_session_id not in k]
        for k in dead_keys:
            self.tracks.pop(k, None)
            self.emitted_plates.pop(k, None)


class SimpleBoxTracker:
    """Lightweight 2D IoU tracker to maintain track_id across frames."""

    def __init__(self, iou_threshold: float = 0.25, max_age: int = 15) -> None:
        self.iou_threshold = iou_threshold
        self.max_age = max_age
        self.next_id = 1
        self.active_tracks: dict[int, dict[str, Any]] = {}

    def update(self, boxes: list[tuple[int, int, int, int]], frame_idx: int) -> list[int]:
        assigned_ids = []
        unmatched_boxes = list(enumerate(boxes))

        for track_id, info in list(self.active_tracks.items()):
            best_iou = 0.0
            best_box_idx = -1
            for idx, box in unmatched_boxes:
                iou = self._iou(info["box"], box)
                if iou > best_iou:
                    best_iou = iou
                    best_box_idx = idx
            if best_iou >= self.iou_threshold and best_box_idx != -1:
                self.active_tracks[track_id] = {"box": boxes[best_box_idx], "last_seen": frame_idx}
                assigned_ids.append((best_box_idx, track_id))
                unmatched_boxes = [item for item in unmatched_boxes if item[0] != best_box_idx]

        for idx, box in unmatched_boxes:
            new_id = self.next_id
            self.next_id += 1
            self.active_tracks[new_id] = {"box": box, "last_seen": frame_idx}
            assigned_ids.append((idx, new_id))

        for track_id, info in list(self.active_tracks.items()):
            if frame_idx - info["last_seen"] > self.max_age:
                del self.active_tracks[track_id]

        assigned_ids.sort(key=lambda x: x[0])
        return [item[1] for item in assigned_ids]

    @staticmethod
    def _iou(box1: tuple[int, int, int, int], box2: tuple[int, int, int, int]) -> float:
        x1 = max(box1[0], box2[0])
        y1 = max(box1[1], box2[1])
        x2 = min(box1[2], box2[2])
        y2 = min(box1[3], box2[3])
        inter = max(0, x2 - x1) * max(0, y2 - y1)
        area1 = max(0, box1[2] - box1[0]) * max(0, box1[3] - box1[1])
        area2 = max(0, box2[2] - box2[0]) * max(0, box2[3] - box2[1])
        union = area1 + area2 - inter
        return float(inter / union) if union > 0 else 0.0


def process_frame(
    recognizer: PlateRecognizer,
    frame: np.ndarray,
    camera_id: str,
    tracker: SimpleBoxTracker,
    fusion: TemporalPlateFusion,
    stream_session_id: str,
    frame_idx: int = 0,
    pts_ms: float = 0.0,
    timestamp: str | None = None,
) -> tuple[list[PlateDetection], list[dict[str, Any]], Any]:
    """Run detection, OCR, tracking, temporal fusion, sightings persistence, and watchlist matching."""
    ensure_sightings_table()
    detections = recognizer.recognize(frame)
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    boxes = [d.box_xyxy for d in detections]
    track_ids = tracker.update(boxes, frame_idx) if boxes else []

    confirmed_sightings = []
    watchlist_matches = []

    for detection, track_id in zip(detections, track_ids):
        track_key = str(TrackKey(str(camera_id), stream_session_id, track_id))
        img_quality = min(1.0, max(0.2, detection.detection_confidence))
        fused = fusion.add_observation(
            track_key=track_key,
            text=detection.text,
            ocr_confidence=detection.ocr_confidence,
            image_quality=img_quality,
            frame_idx=frame_idx,
            pts_ms=pts_ms,
        )

        if fused is not None:
            plate_num = fused["plate_number"]
            conf = fused["confidence"]

            if fusion.should_emit(track_key, plate_num):
                # Save crop snapshot
                snapshot_path = None
                bx1, by1, bx2, by2 = detection.box_xyxy
                if bx2 > bx1 and by2 > by1 and frame is not None:
                    h, w = frame.shape[:2]
                    crop = frame[max(0, by1):min(h, by2), max(0, bx1):min(w, bx2)]
                    if crop.size > 0:
                        snap_dir = Path("alerts_snapshots")
                        snap_dir.mkdir(parents=True, exist_ok=True)
                        snap_name = f"sighting_{camera_id}_{plate_num}_{int(time.time()*1000)}.jpg"
                        snap_file = snap_dir / snap_name
                        cv2.imwrite(str(snap_file), crop)
                        snapshot_path = str(snap_file)

                # Persist sighting to SQLite
                sighting_id = record_sighting(
                    camera_id=camera_id,
                    plate_number=plate_num,
                    confidence=conf,
                    timestamp=timestamp,
                    pts_ms=pts_ms,
                    snapshot_path=snapshot_path,
                    track_key=track_key,
                )
                fused["sighting_id"] = sighting_id
                confirmed_sightings.append(fused)

                # Check watchlist
                match = find_watchlist_match(plate_num)
                if match is not None:
                    watchlist_matches.append(match)
                    emit_watchlist_alert(
                        match,
                        camera_id=camera_id,
                        timestamp_pts_ms=pts_ms,
                        frame=frame,
                    )

    return detections, confirmed_sightings, watchlist_matches


def run_video(
    video_path: str,
    camera_id: str = "cam_test",
    max_frames: int = 300,
    stride: int = 5,
    stream_session_id: str | None = None,
) -> dict[str, Any]:
    """Process a local video file with ANPR, temporal fusion, and sightings recording."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise IOError(f"Could not open video: {video_path}")

    recognizer = PlateRecognizer()
    tracker = SimpleBoxTracker()
    fusion = TemporalPlateFusion()
    session_id = stream_session_id or str(uuid.uuid4())[:8]

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    frame_idx = 0
    processed_count = 0
    all_sightings = []
    all_matches = []

    try:
        while frame_idx < max_frames:
            ret, frame = cap.read()
            if not ret or frame is None:
                break
            frame_idx += 1
            if frame_idx % stride != 0:
                continue

            processed_count += 1
            pts_ms = float(frame_idx / fps * 1000.0)
            detections, sightings, matches = process_frame(
                recognizer=recognizer,
                frame=frame,
                camera_id=camera_id,
                tracker=tracker,
                fusion=fusion,
                stream_session_id=session_id,
                frame_idx=frame_idx,
                pts_ms=pts_ms,
            )
            if sightings:
                all_sightings.extend(sightings)
            if matches:
                all_matches.extend(matches)
    finally:
        cap.release()

    return {
        "camera_id": camera_id,
        "frames_seen": frame_idx,
        "frames_processed": processed_count,
        "sightings_recorded": len(all_sightings),
        "sightings": all_sightings,
        "watchlist_matches": len(all_matches),
    }


def run_live(
    cameras=None,
    max_frames_per_camera: int = 200,
    stride: int = ANPR_FRAME_STRIDE,
    poll_timeout: float = 5.0,
) -> dict:
    """Consume every camera from the ingest list and run ANPR on its frames."""
    from anpr.live_capture import (
        ResilientRTSPCapture,
        fetch_ingest_cameras,
        filter_rtsp_cameras,
    )

    if cameras is None:
        cameras = fetch_ingest_cameras()
    rtsp_cameras = filter_rtsp_cameras(cameras)
    print(f"[ANPR LIVE] {len(rtsp_cameras)} RTSP camera(s) to process")
    results = {}

    for camera in rtsp_cameras:
        print(f"\n=== [ANPR LIVE] camera {camera.camera_id} ({camera.name}) ===")
        capture = ResilientRTSPCapture(camera.source, camera_id=camera.camera_id)
        capture.start()
        recognizer = PlateRecognizer()
        tracker = SimpleBoxTracker()
        fusion = TemporalPlateFusion()

        seen = 0
        processed = 0
        sightings_count = 0
        matches_count = 0
        deadline_frames = max_frames_per_camera * stride + stride * 10

        try:
            while seen < deadline_frames and processed < max_frames_per_camera:
                item = capture.read_latest(timeout=poll_timeout)
                if item is None:
                    print(
                        f"[ANPR LIVE] no frame from camera {camera.camera_id} "
                        f"within {poll_timeout:.0f}s; stopping this camera."
                    )
                    break
                frame, pts_ms, stream_session_id = item
                seen += 1
                if seen % stride != 0:
                    continue
                processed += 1
                detections, sightings, matches = process_frame(
                    recognizer=recognizer,
                    frame=frame,
                    camera_id=str(camera.camera_id),
                    tracker=tracker,
                    fusion=fusion,
                    stream_session_id=stream_session_id,
                    frame_idx=seen,
                    pts_ms=pts_ms,
                )
                sightings_count += len(sightings)
                matches_count += len(matches)
                for s in sightings:
                    print(
                        f"[ANPR SIGHTING] cam={camera.camera_id} pts={pts_ms:.0f}ms "
                        f"plate={s['plate_number']} conf={s['confidence']} "
                        f"evidence={s['evidence_count']}"
                    )
        finally:
            capture.stop()

        results[str(camera.camera_id)] = {
            "frames_seen": seen,
            "frames_processed": processed,
            "sightings_recorded": sightings_count,
            "watchlist_matches": matches_count,
            "last_pts_ms": capture.last_pts_ms,
            "reconnects": capture.reconnects,
            "frames_read_by_producer": capture.frames_read,
        }
        print(
            f"[ANPR LIVE] camera {camera.camera_id} done: "
            f"producer_read={capture.frames_read} seen={seen} "
            f"processed={processed} sightings={sightings_count} "
            f"matches={matches_count} reconnects={capture.reconnects}"
        )
    return results


def main() -> None:
    print("[ANPR LIVE] starting live ANPR (Sentinel Sandbox path)")
    run_live()


if __name__ == "__main__":
    main()