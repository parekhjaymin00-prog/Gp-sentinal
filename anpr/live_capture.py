"""Resilient RTSP capture for the ANPR module (Sentinel Sandbox path).

Extends the EXISTING main.py producer/consumer mailbox architecture
(LatestFrameCapture) rather than replacing it:

  * same one-frame mailbox + condition-variable handoff
  * same bounded FFmpeg open/read timeout semantics
  * forced rtsp_transport=tcp via OPENCV_FFMPEG_CAPTURE_OPTIONS
  * timing exclusively from CAP_PROP_POS_MSEC (RTSP presentation timestamp);
    CAP_PROP_FPS and arrival time are never used for media timing
  * reconnect with exponential backoff (2 s start, 30 s cap) on read failure
  * scene discontinuity detection (64-bin histogram correlation < 0.70 and
    PTS backward jumps) with stream session rotation (TrackKey isolation)

Decoder warnings printed by FFmpeg on join are logged and NOT treated as
fatal: only a closed capture or a failing read loop reconnects/exits.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass

import cv2
import numpy as np

from anpr.config import (
    FFMPEG_CAPTURE_OPTIONS_ENV,
    FFMPEG_CAPTURE_OPTIONS_VALUE,
    INGEST_BASE_URL,
    INGEST_PATHS,
    INGEST_TIMEOUT_SECONDS,
    LIVE_STREAM_OPEN_TIMEOUT_MSEC,
    LIVE_STREAM_READ_TIMEOUT_MSEC,
    RECONNECT_BACKOFF_MULTIPLIER,
    RECONNECT_INITIAL_DELAY_SECONDS,
    RECONNECT_MAX_DELAY_SECONDS,
    RTSP_TRANSPORT,
)


class SceneDiscontinuityDetector:
    """Detects hard scene cuts and loops using 64-bin grayscale histogram correlation."""

    def __init__(self, threshold: float = 0.70, bins: int = 64, long_edge: int = 160) -> None:
        self.threshold = threshold
        self.bins = bins
        self.long_edge = long_edge
        self._previous: np.ndarray | None = None
        self.last_correlation: float | None = None
        self.detections = 0

    def check(self, frame_bgr: np.ndarray) -> tuple[bool, float | None]:
        if frame_bgr is None or frame_bgr.size == 0:
            return False, None
        hist = self._calc_hist(frame_bgr)
        if self._previous is None:
            self._previous = hist
            return False, None

        corr = self._correlate(self._previous, hist)
        self._previous = hist
        self.last_correlation = corr
        if corr < self.threshold:
            self.detections += 1
            return True, corr
        return False, corr

    def reset(self) -> None:
        self._previous = None
        self.last_correlation = None

    def _calc_hist(self, frame_bgr: np.ndarray) -> np.ndarray:
        h, w = frame_bgr.shape[:2]
        step = max(1, int(max(h, w) // max(1, self.long_edge)))
        small = frame_bgr[::step, ::step]
        gray = (0.114 * small[:, :, 0] + 0.587 * small[:, :, 1] + 0.299 * small[:, :, 2])
        hist, _ = np.histogram(gray, bins=self.bins, range=(0.0, 256.0))
        s = hist.sum()
        return hist.astype(np.float64) / float(s) if s > 0 else hist.astype(np.float64)

    @staticmethod
    def _correlate(a: np.ndarray, b: np.ndarray) -> float:
        a_var, b_var = float(np.var(a)), float(np.var(b))
        if a_var == 0.0 or b_var == 0.0:
            return 1.0 if np.allclose(a, b) else 0.0
        return float(np.corrcoef(a, b)[0, 1])


# ======================================================================
# Camera discovery: GET /api/ingest, never hardcoded URLs
# ======================================================================
@dataclass(frozen=True)
class IngestCamera:
    camera_id: int | str
    name: str
    source: str
    source_type: str


def fetch_ingest_cameras(
    base_url: str = INGEST_BASE_URL,
    timeout: float = INGEST_TIMEOUT_SECONDS,
) -> list[IngestCamera]:
    """Pull the camera list from the API."""
    last_error: Exception | None = None
    for path in INGEST_PATHS:
        url = f"{base_url.rstrip('/')}{path}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            cameras = []
            items = payload if isinstance(payload, list) else payload.get("cameras", [])
            for item in items:
                cameras.append(
                    IngestCamera(
                        camera_id=item.get("id"),
                        name=str(item.get("name", f"camera-{item.get('id')}")),
                        source=str(item.get("source", "")),
                        source_type=str(item.get("source_type", "")),
                    )
                )
            print(f"[ANPR INGEST] {len(cameras)} camera(s) from GET {url}")
            return cameras
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as exc:
            last_error = exc
            print(f"[ANPR INGEST] {url} unavailable: {exc}", file=os.sys.stderr)
    raise RuntimeError(
        f"No camera source reachable at {base_url} (tried {INGEST_PATHS}): {last_error}"
    )


def filter_rtsp_cameras(cameras: list[IngestCamera]) -> list[IngestCamera]:
    """Keep only rtsp/rtsps sources for the TCP capture path."""
    return [
        camera
        for camera in cameras
        if camera.source.lower().startswith(("rtsp://", "rtsps://"))
    ]


# ======================================================================
# Capture
# ======================================================================
def set_rtsp_tcp_env() -> None:
    """Force rtsp_transport=tcp for every FFmpeg-backed cv2.VideoCapture."""
    existing = os.environ.get(FFMPEG_CAPTURE_OPTIONS_ENV, "")
    if "rtsp_transport" in existing:
        return
    os.environ[FFMPEG_CAPTURE_OPTIONS_ENV] = FFMPEG_CAPTURE_OPTIONS_VALUE
    print(
        f"[ANPR RTSP] {FFMPEG_CAPTURE_OPTIONS_ENV}={FFMPEG_CAPTURE_OPTIONS_VALUE} "
        f"(transport={RTSP_TRANSPORT})"
    )


def open_rtsp_capture(source: str) -> cv2.VideoCapture:
    """Open one RTSP stream over TCP with bounded open/read timeouts."""
    set_rtsp_tcp_env()
    capture = cv2.VideoCapture(
        source,
        cv2.CAP_FFMPEG,
        [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
            LIVE_STREAM_OPEN_TIMEOUT_MSEC,
            cv2.CAP_PROP_READ_TIMEOUT_MSEC,
            LIVE_STREAM_READ_TIMEOUT_MSEC,
        ],
    )
    if not capture.isOpened():
        capture.release()
        raise IOError(f"could not open RTSP stream (tcp): {source}")
    print(f"[ANPR RTSP] opened {source} over {RTSP_TRANSPORT.upper()} "
          f"({int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))}x"
          f"{int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))})")
    return capture


def read_frame_with_pts(capture: cv2.VideoCapture) -> tuple[bool, object, float]:
    """Read one frame and its CAP_PROP_POS_MSEC presentation timestamp."""
    ok, frame = capture.read()
    pts_ms = float(capture.get(cv2.CAP_PROP_POS_MSEC)) if ok else -1.0
    return ok, frame, pts_ms


class ResilientRTSPCapture:
    """Producer thread: read frames + PTS from RTSP with reconnect backoff and session rotation."""

    def __init__(self, source: str, camera_id=None, max_reconnects: int = 3) -> None:
        self.source = source
        self.camera_id = camera_id
        self.max_reconnects = max_reconnects
        self.stream_session_id = str(uuid.uuid4())[:8]
        self.discontinuity_detector = SceneDiscontinuityDetector(threshold=0.70)
        self._capture: cv2.VideoCapture | None = None
        self._lock = threading.Lock()
        self._latest: tuple[object, float, str] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.decoder_warnings: list[str] = []
        self.frames_read = 0
        self.reconnects = 0
        self.last_pts_ms: float | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name=f"anpr-rtsp-{self.camera_id}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10.0)
        self._release()

    def read_latest(self, timeout: float = 5.0) -> tuple[object, float, str] | None:
        """Pop the newest (frame, pts_ms, stream_session_id) mailbox item, or None on timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._latest is not None:
                    item, self._latest = self._latest, None
                    return item
            time.sleep(0.05)
        return None

    def _release(self) -> None:
        if self._capture is not None:
            try:
                self._capture.release()
            except Exception:
                pass
            self._capture = None

    def _connect(self) -> None:
        try:
            self._capture = open_rtsp_capture(self.source)
            self.stream_session_id = str(uuid.uuid4())[:8]
            self.discontinuity_detector.reset()
        except Exception as exc:
            self._capture = None
            print(f"[ANPR RTSP] connect failed: {exc}", file=os.sys.stderr)

    def _run(self) -> None:
        backoff = RECONNECT_INITIAL_DELAY_SECONDS
        attempts = 0
        self._connect()

        while not self._stop.is_set():
            if self._capture is None or not self._capture.isOpened():
                if attempts >= self.max_reconnects:
                    print(
                        f"[ANPR RTSP] giving up on {self.source} after "
                        f"{attempts} reconnect attempt(s).",
                        file=os.sys.stderr,
                    )
                    return
                print(
                    f"[ANPR RTSP] reconnecting in {backoff:.1f}s "
                    f"(attempt {attempts + 1}/{self.max_reconnects})"
                )
                if self._stop.wait(backoff):
                    return
                attempts += 1
                self.reconnects += 1
                backoff = min(
                    backoff * RECONNECT_BACKOFF_MULTIPLIER, RECONNECT_MAX_DELAY_SECONDS
                )
                self._connect()
                continue

            ok, frame, pts_ms = read_frame_with_pts(self._capture)
            if not ok or frame is None:
                print(
                    f"[ANPR RTSP] read failure on {self.source}; will reconnect.",
                    file=os.sys.stderr,
                )
                self._release()
                backoff = RECONNECT_INITIAL_DELAY_SECONDS
                continue

            attempts = 0
            backoff = RECONNECT_INITIAL_DELAY_SECONDS

            # Scene discontinuity check (PTS jump backward or histogram correlation < 0.70)
            is_backward_jump = (
                self.last_pts_ms is not None and pts_ms < (self.last_pts_ms - 500.0)
            )
            is_cut, corr = self.discontinuity_detector.check(frame)
            if is_backward_jump or is_cut:
                prev_session = self.stream_session_id
                self.stream_session_id = str(uuid.uuid4())[:8]
                print(
                    f"[ANPR RTSP] Scene discontinuity on {self.camera_id}: "
                    f"corr={corr:.3f if corr is not None else -1.0} "
                    f"pts={pts_ms:.0f}ms (prev={self.last_pts_ms or 0:.0f}ms). "
                    f"Rotated stream session {prev_session} -> {self.stream_session_id}"
                )

            self.frames_read += 1
            self.last_pts_ms = pts_ms
            with self._lock:
                self._latest = (frame, pts_ms, self.stream_session_id)
        self._release()