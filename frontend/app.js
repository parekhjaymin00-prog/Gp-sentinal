(function emergencyDashboard(global) {
  "use strict";

  const POLL_INTERVAL_MS = 5000;
  const API_BASE =
    typeof global.location !== "undefined" && global.location.protocol === "file:"
      ? "http://127.0.0.1:8000"
      : typeof global.location !== "undefined"
        ? global.location.origin
        : "http://127.0.0.1:8000";

  let journeyMap = null;
  let journeyLayerGroup = null;

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function formatConfidence(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "Unknown";
    const percentage = number <= 1 ? number * 100 : number;
    return `${percentage.toFixed(1)}%`;
  }

  function formatTimestamp(value) {
    if (!value) return "Unknown time";
    const normalized = String(value).includes("T")
      ? String(value)
      : `${String(value).replace(" ", "T")}Z`;
    const date = new Date(normalized);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
  }

  function buildAlertFeedHtml(alerts) {
    if (!Array.isArray(alerts) || alerts.length === 0) {
      return '<p class="empty-state">No incidents have been logged yet.</p>';
    }

    return alerts
      .map((alert) => {
        const type = String(alert.type || "unknown").toLowerCase();
        const isWatchlist = type.includes("watchlist");
        const detailsText = String(alert.details || "");
        
        let plateStr = "";
        const plateMatch = detailsText.match(/plate=([A-Z0-9]+)/i);
        if (plateMatch) {
          plateStr = plateMatch[1].toUpperCase();
        }

        let watchlistExtraHtml = "";
        if (isWatchlist) {
          watchlistExtraHtml = `
            <div class="sop-recommendation">
              <span>🚨 <strong>POLICE DISPATCH ADVICE:</strong> Priority intercept suspect vehicle ${escapeHtml(plateStr || "TARGET")}. Alert nearest Gandhinagar/Ahmedabad highway patrol unit immediately.</span>
            </div>
            ${plateStr ? `<button type="button" class="quick-journey-btn" onclick="window.trackPlate('${escapeHtml(plateStr)}')">📍 Track Vehicle Journey</button>` : ""}
          `;
        }

        return `
          <article class="alert-card alert-${escapeHtml(type)}" data-alert-id="${escapeHtml(alert.id)}">
            <div class="alert-card-header">
              <strong>${isWatchlist ? "🚨 " : ""}${escapeHtml(type.toUpperCase())}</strong>
              <span class="confidence">${escapeHtml(formatConfidence(alert.confidence))}</span>
            </div>
            <time datetime="${escapeHtml(alert.timestamp)}">${escapeHtml(formatTimestamp(alert.timestamp))}</time>
            <p>${escapeHtml(detailsText || "No detector metrics recorded")}</p>
            ${watchlistExtraHtml}
            <span class="incident-id">Incident #${escapeHtml(alert.id)}</span>
          </article>`;
      })
      .join("");
  }

  function buildCameraListHtml(cameras) {
    if (!Array.isArray(cameras) || cameras.length === 0) {
      return '<p class="empty-state">No camera sources added yet.</p>';
    }

    return cameras
      .map(
        (camera) => `
          <article class="camera-card" data-camera-id="${escapeHtml(camera.id)}">
            <div>
              <strong>${escapeHtml(camera.name)}</strong>
              <span class="source-type">${escapeHtml(camera.source_type)}</span>
            </div>
            <code title="${escapeHtml(camera.source)}">${escapeHtml(camera.source)}</code>
          </article>`,
      )
      .join("");
  }

  async function fetchJson(path, options) {
    const response = await fetch(`${API_BASE}${path}`, options);
    if (!response.ok) {
      const message = await response.text();
      throw new Error(`API error ${response.status}: ${message}`);
    }
    return response.json();
  }

  function setStatus(online) {
    const dot = document.getElementById("status-dot");
    const label = document.getElementById("api-status");
    if (!dot || !label) return;

    if (online) {
      dot.className = "status-dot status-online";
      label.textContent = "Live Stream & Database Online";
    } else {
      dot.className = "status-dot status-offline";
      label.textContent = "API Disconnected";
    }
  }

  async function refreshAlerts() {
    try {
      const alerts = await fetchJson("/alerts");
      document.getElementById("alert-feed").innerHTML = buildAlertFeedHtml(alerts);
      const lastRefresh = document.getElementById("last-refresh");
      if (lastRefresh) {
        lastRefresh.textContent = `Updated ${new Date().toLocaleTimeString()}`;
      }
      setStatus(true);
    } catch (error) {
      setStatus(false);
      console.error("Alert refresh failed:", error);
    }
  }

  async function refreshCameras() {
    try {
      const cameras = await fetchJson("/cameras");
      document.getElementById("camera-list").innerHTML = buildCameraListHtml(cameras);
    } catch (error) {
      console.error("Camera refresh failed:", error);
    }
  }

  async function submitCamera(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const nameInput = form.elements.namedItem("name");
    const sourceInput = form.elements.namedItem("source");
    const message = document.getElementById("camera-form-message");

    const payload = {
      name: nameInput.value.trim(),
      source: sourceInput.value.trim(),
    };

    try {
      await fetchJson("/cameras", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      message.className = "form-message success-state";
      message.textContent = `Registered camera "${payload.name}".`;
      form.reset();
      refreshCameras();
    } catch (error) {
      message.className = "form-message error-state";
      message.textContent = `Failed to add camera: ${error.message}`;
    }
  }

  let currentJobId = null;
  let pollInterval = null;

  async function uploadVideoForAnalysis(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const fileInput = document.getElementById("video-file");
    const message = document.getElementById("upload-form-message");

    if (!fileInput.files || fileInput.files.length === 0) {
      message.className = "form-message error-state";
      message.textContent = "Please select a video file.";
      return;
    }

    const file = fileInput.files[0];
    const formData = new FormData();
    formData.append("video", file);

    try {
      message.className = "form-message";
      message.textContent = "Uploading video...";

      const response = await fetch(`${API_BASE}/videos/analyze`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const body = await response.json();
        throw new Error(body.detail || `${response.status} ${response.statusText}`);
      }

      const job = await response.json();
      currentJobId = job.job_id;

      message.className = "form-message success-state";
      message.textContent = `Upload complete. Analysis started (Job ID: ${job.job_id.slice(0, 8)}...)`;
      form.reset();

      document.getElementById("batch-status").innerHTML = `
        <div class="status-card">
          <strong>Status:</strong> ${escapeHtml(job.status)}
          <br>
          <strong>Job ID:</strong> <code>${escapeHtml(job.job_id)}</code>
          <br>
          <strong>Video:</strong> ${escapeHtml(job.video_filename)}
        </div>
      `;

      document.getElementById("batch-results").innerHTML = "";

      if (pollInterval) clearInterval(pollInterval);
      pollInterval = setInterval(() => pollJobStatus(job.job_id), 3000);
      pollJobStatus(job.job_id);

    } catch (error) {
      message.className = "form-message error-state";
      message.textContent = `Upload failed: ${error.message}`;
    }
  }

  async function pollJobStatus(jobId) {
    try {
      const job = await fetchJson(`/videos/analyze/${jobId}`);
      
      let statusHtml = `
        <div class="status-card status-${escapeHtml(job.status)}">
          <strong>Status:</strong> ${escapeHtml(job.status.toUpperCase())}
          <br>
          <strong>Job ID:</strong> <code>${escapeHtml(job.job_id)}</code>
          <br>
          <strong>Video:</strong> ${escapeHtml(job.video_filename || "Unknown")}
      `;

      if (job.progress) {
        const percent = (job.progress.frames_processed / job.progress.total_frames * 100).toFixed(1);
        statusHtml += `
          <br>
          <strong>Progress:</strong> ${escapeHtml(job.progress.frames_processed)} / ${escapeHtml(job.progress.total_frames)} frames (${percent}%)
        `;
      }

      statusHtml += `</div>`;
      document.getElementById("batch-status").innerHTML = statusHtml;

      if (job.status === "completed") {
        if (pollInterval) {
          clearInterval(pollInterval);
          pollInterval = null;
        }
      } else if (job.status === "failed") {
        if (pollInterval) {
          clearInterval(pollInterval);
          pollInterval = null;
        }
      }
    } catch (error) {
      console.error("Failed to poll job status:", error);
    }
  }

  // ============================================================
  // TASK 7: VEHICLE JOURNEY TRACKER & LEAFLET MAP
  // ============================================================

  function initJourneyMap() {
    if (typeof L === "undefined") {
      console.warn("Leaflet library not loaded yet.");
      return;
    }
    const mapContainer = document.getElementById("journey-map");
    if (!mapContainer || journeyMap) return;

    // Gandhinagar / Ahmedabad Center
    journeyMap = L.map("journey-map").setView([23.235, 72.655], 12);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "© OpenStreetMap contributors | Gujarat Police Sentinel",
    }).addTo(journeyMap);

    journeyLayerGroup = L.layerGroup().addTo(journeyMap);
  }

  async function trackPlate(plateQuery) {
    initJourneyMap();
    const plate = String(plateQuery || "").trim().toUpperCase();
    if (!plate) return;

    const input = document.getElementById("journey-plate-input");
    if (input) input.value = plate;

    const summaryEl = document.getElementById("journey-summary");
    const timelineEl = document.getElementById("journey-timeline");
    const badgeEl = document.getElementById("journey-status-badge");

    summaryEl.style.display = "block";
    summaryEl.innerHTML = `<em>Querying cross-camera sightings for plate <strong>${escapeHtml(plate)}</strong>…</em>`;

    try {
      const data = await fetchJson(`/journey/${encodeURIComponent(plate)}`);
      if (journeyLayerGroup) journeyLayerGroup.clearLayers();

      if (!data.sightings || data.sightings.length === 0) {
        summaryEl.innerHTML = `<strong>Plate:</strong> ${escapeHtml(data.plate)} | <span style="color:#94a3b8;">No sightings recorded across Sentinel grid cameras.</span>`;
        timelineEl.innerHTML = `<p class="empty-state">No sightings found in SQLite database for plate <strong>${escapeHtml(plate)}</strong>.</p>`;
        if (badgeEl) badgeEl.innerHTML = "";
        return;
      }

      const isFeasible = data.overall_feasible;
      const statusBadgeHtml = `<span class="journey-summary-badge ${isFeasible ? "badge-feasible" : "badge-infeasible"}">${isFeasible ? "✓ FEASIBLE JOURNEY" : "⚠ IMPLAUSIBLE SPEED JUMP"}</span>`;

      if (badgeEl) badgeEl.innerHTML = statusBadgeHtml;

      summaryEl.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
          <div>
            <strong style="color:#54d2ff; font-size: 1.1rem; letter-spacing: 0.05em;">${escapeHtml(data.plate)}</strong>
            <span style="color: #94a3b8; font-size: 0.85rem; margin-left: 12px;">Total Sightings: <strong>${data.sighting_count}</strong> across <strong>${data.segments ? data.segments.length + 1 : 1}</strong> cameras</span>
          </div>
          <div>${statusBadgeHtml}</div>
        </div>
        <p style="margin: 6px 0 0 0; font-size: 0.75rem; color: #64748b;">${escapeHtml(data.disclaimer)}</p>
      `;

      // Render chronological timeline
      let timelineHtml = "";
      const latlngs = [];

      data.sightings.forEach((s, idx) => {
        latlngs.push([s.lat, s.lon]);

        if (journeyMap && journeyLayerGroup) {
          const marker = L.circleMarker([s.lat, s.lon], {
            radius: 8,
            color: "#38bdf8",
            fillColor: "#0284c7",
            fillOpacity: 0.9,
            weight: 2,
          });
          marker.bindPopup(`
            <strong>${escapeHtml(s.camera_name || s.camera_id)}</strong><br>
            Time: ${escapeHtml(s.timestamp)}<br>
            Plate: <code>${escapeHtml(s.plate_number)}</code><br>
            Confidence: ${(s.confidence * 100).toFixed(1)}%<br>
            Hop #${idx + 1}
          `);
          journeyLayerGroup.addLayer(marker);
        }

        timelineHtml += `
          <div class="timeline-item">
            <div class="timeline-icon">${idx + 1}</div>
            <div class="timeline-details">
              <div class="timeline-cam">${escapeHtml(s.camera_name || s.camera_id)} <span style="font-size:0.75rem; color:#64748b;">(${escapeHtml(s.camera_id)})</span></div>
              <div class="timeline-meta">${escapeHtml(s.timestamp)} · Confidence: ${(s.confidence * 100).toFixed(1)}%</div>
            </div>
          </div>
        `;

        if (data.segments && data.segments[idx]) {
          const seg = data.segments[idx];
          const speedClass = seg.feasible ? "speed-feasible" : "speed-infeasible";
          timelineHtml += `
            <div style="padding: 6px 14px 6px 44px; font-size: 0.8rem; border-left: 3px ${seg.feasible ? "solid #10b981" : "dashed #ef4444"}; margin-left: 24px; background: #0c121e;">
              Corridor hop: <strong>${seg.distance_km} km</strong> in <strong>${seg.elapsed_seconds}s</strong>
              ➔ Required Speed: <span class="${speedClass}"><strong>${seg.required_speed_kmh} km/h</strong> (${seg.feasible ? "Feasible" : "Infeasible"})</span>
              ${seg.note ? `<div style="color: #f87171; font-size: 0.75rem; margin-top: 2px;">⚠ ${escapeHtml(seg.note)}</div>` : ""}
            </div>
          `;
        }
      });

      timelineEl.innerHTML = timelineHtml;

      // Draw segment polylines on map
      if (journeyMap && journeyLayerGroup && data.segments) {
        data.segments.forEach((seg, i) => {
          const p1 = [data.sightings[i].lat, data.sightings[i].lon];
          const p2 = [data.sightings[i + 1].lat, data.sightings[i + 1].lon];
          const polyline = L.polyline([p1, p2], {
            color: seg.feasible ? "#10b981" : "#ef4444",
            weight: 4,
            opacity: 0.85,
            dashArray: seg.feasible ? null : "8, 8",
          });
          polyline.bindPopup(`Hop: ${escapeHtml(seg.from_camera_name)} ➔ ${escapeHtml(seg.to_camera_name)}<br>Distance: ${seg.distance_km} km<br>Speed: ${seg.required_speed_kmh} km/h<br>${seg.feasible ? "Feasible" : "Implausible Jump"}`);
          journeyLayerGroup.addLayer(polyline);
        });

        if (latlngs.length > 0) {
          const bounds = L.latLngBounds(latlngs);
          journeyMap.fitBounds(bounds, { padding: [40, 40] });
        }
      }

    } catch (err) {
      summaryEl.innerHTML = `<span style="color:#ef4444;">Error reconstructing journey: ${escapeHtml(err.message)}</span>`;
      timelineEl.innerHTML = "";
    }
  }

  // Expose trackPlate globally for onclick handlers in alert feed
  global.trackPlate = trackPlate;

  function initialize() {
    document.getElementById("refresh-alerts").addEventListener("click", refreshAlerts);
    document.getElementById("camera-form").addEventListener("submit", submitCamera);
    document.getElementById("upload-form").addEventListener("submit", uploadVideoForAnalysis);

    const journeyForm = document.getElementById("journey-search-form");
    if (journeyForm) {
      journeyForm.addEventListener("submit", (e) => {
        e.preventDefault();
        const input = document.getElementById("journey-plate-input");
        trackPlate(input.value);
      });
    }

    refreshAlerts();
    refreshCameras();
    initJourneyMap();
    global.setInterval(refreshAlerts, POLL_INTERVAL_MS);
  }

  const publicApi = {
    API_BASE,
    POLL_INTERVAL_MS,
    buildAlertFeedHtml,
    buildCameraListHtml,
    escapeHtml,
    formatConfidence,
    trackPlate,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = publicApi;
  }
  global.EmergencyDashboard = publicApi;

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", initialize);
    } else {
      initialize();
    }
  }
})(typeof globalThis !== "undefined" ? globalThis : this);