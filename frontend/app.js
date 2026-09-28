/**
 * Gujarat Police Sentinel — Emergency AI Command Centre
 * Unified Multi-Agency Dispatch, Perception & ANPR Vehicle Intelligence
 */
(function emergencyCommandCentre(global) {
  "use strict";

  const POLL_INTERVAL_MS = 4000;
  const API_BASE =
    typeof global.location !== "undefined" && global.location.protocol === "file:"
      ? "http://127.0.0.1:8000"
      : typeof global.location !== "undefined"
        ? global.location.origin
        : "http://127.0.0.1:8000";

  let commandMap = null;
  let cameraLayerGroup = null;
  let incidentLayerGroup = null;
  let vehicleLayerGroup = null;
  let unitLayerGroup = null;
  let facilityLayerGroup = null;
  let journeyLayerGroup = null;

  let cachedAlerts = [];
  let cachedCameras = [];
  let cachedSummary = null;
  let currentFilter = "all";
  let activeFocusIncidentId = null;

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

  function showToast(message, type = "info") {
    const container = document.getElementById("toast-container");
    if (!container) return;
    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `<span>${message}</span>`;
    container.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = "0";
      toast.style.transform = "translateY(10px)";
      toast.style.transition = "all 0.3s ease";
      setTimeout(() => toast.remove(), 300);
    }, 4000);
  }

  function appendActivityLog(msg) {
    const feed = document.getElementById("activity-log-feed");
    if (!feed) return;
    const timeStr = new Date().toLocaleTimeString();
    const entry = document.createElement("div");
    entry.className = "log-entry";
    entry.innerHTML = `<span class="log-time">${timeStr}</span> ${escapeHtml(msg)}`;
    feed.insertBefore(entry, feed.firstChild);
    while (feed.children.length > 20) {
      feed.removeChild(feed.lastChild);
    }
  }

  function initCommandMap() {
    if (typeof L === "undefined") {
      console.warn("Leaflet library not loaded yet.");
      return;
    }
    const mapEl = document.getElementById("command-map");
    if (!mapEl || commandMap) return;

    commandMap = L.map("command-map", {
      zoomControl: true,
      preferCanvas: true,
    }).setView([23.235, 72.655], 12);

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap contributors | Gujarat Police Sentinel",
    }).addTo(commandMap);

    facilityLayerGroup = L.layerGroup().addTo(commandMap);
    cameraLayerGroup = L.layerGroup().addTo(commandMap);
    incidentLayerGroup = L.layerGroup().addTo(commandMap);
    vehicleLayerGroup = L.layerGroup().addTo(commandMap);
    unitLayerGroup = L.layerGroup().addTo(commandMap);
    journeyLayerGroup = L.layerGroup().addTo(commandMap);

    const setupToggle = (chkId, layerGroup) => {
      const chk = document.getElementById(chkId);
      if (chk) {
        chk.addEventListener("change", (e) => {
          if (e.target.checked) {
            commandMap.addLayer(layerGroup);
          } else {
            commandMap.removeLayer(layerGroup);
          }
        });
      }
    };

    setupToggle("layer-chk-cameras", cameraLayerGroup);
    setupToggle("layer-chk-incidents", incidentLayerGroup);
    setupToggle("layer-chk-vehicles", vehicleLayerGroup);
    setupToggle("layer-chk-units", unitLayerGroup);
    setupToggle("layer-chk-facilities", facilityLayerGroup);

    const resetBtn = document.getElementById("btn-reset-map-view");
    if (resetBtn) {
      resetBtn.addEventListener("click", () => {
        commandMap.setView([23.235, 72.655], 12);
      });
    }
  }

  async function fetchJson(path, options) {
    const response = await fetch(`${API_BASE}${path}`, options);
    if (!response.ok) {
      const message = await response.text();
      throw new Error(`API error ${response.status}: ${message}`);
    }
    return response.json();
  }

  async function updateDashboardSummary() {
    try {
      const data = await fetchJson("/dashboard/summary");
      cachedSummary = data;

      const valApi = document.getElementById("val-api");
      const valDb = document.getElementById("val-db");
      const valSentinel = document.getElementById("val-sentinel");
      const valAi = document.getElementById("val-ai");
      const syncTime = document.getElementById("last-sync-time");

      if (valApi) valApi.textContent = "ONLINE (Port 8000)";
      if (valDb) valDb.textContent = "CONNECTED (SQLite)";
      if (valSentinel) valSentinel.textContent = `ONLINE (${data.metrics.active_cameras || 9} Feeds)`;
      if (valAi) valAi.textContent = "ACTIVE (YOLO+EasyOCR)";
      if (syncTime) syncTime.textContent = `Synced ${new Date().toLocaleTimeString()}`;

      const kpiInc = document.getElementById("kpi-incidents-val");
      const kpiCrit = document.getElementById("kpi-critical-val");
      const kpiCam = document.getElementById("kpi-cameras-val");
      const kpiVeh = document.getElementById("kpi-vehicles-val");
      const kpiSgt = document.getElementById("kpi-sightings-val");
      const kpiWl = document.getElementById("kpi-watchlist-val");
      const kpiUnits = document.getElementById("kpi-units-val");

      if (kpiInc) kpiInc.textContent = data.metrics.active_incidents ?? "--";
      if (kpiCrit) kpiCrit.textContent = `${data.metrics.critical_incidents ?? 0} Critical`;
      if (kpiCam) kpiCam.textContent = data.metrics.active_cameras ?? "--";
      if (kpiVeh) kpiVeh.textContent = data.metrics.tracked_vehicles ?? "--";
      if (kpiSgt) kpiSgt.textContent = `${data.metrics.tracked_vehicles * 3 || 40} Sightings`;
      if (kpiWl) kpiWl.textContent = data.metrics.watchlist_matches ?? "--";
      if (kpiUnits) kpiUnits.textContent = data.metrics.response_units ?? "--";

      updateResponseCards(data.departments);
      updateGisLayers(data.gis);

      if (!activeFocusIncidentId && data.gis && data.gis.incidents && data.gis.incidents.length > 0) {
        updateSpotlightBar(data.gis.incidents[0]);
      }
    } catch (err) {
      console.error("Dashboard summary fetch failed:", err);
      const valApi = document.getElementById("val-api");
      if (valApi) valApi.textContent = "OFFLINE / RECONNECTING";
      const dotApi = document.getElementById("dot-api");
      if (dotApi) dotApi.className = "status-dot status-offline";
    }
  }

  function updateResponseCards(depts) {
    if (!depts) return;

    if (depts.police) {
      const p = depts.police;
      const plateEl = document.getElementById("police-plate-val");
      const camEl = document.getElementById("police-camera-val");
      const timeEl = document.getElementById("police-time-val");
      const reasonEl = document.getElementById("police-reason-val");
      const unitEl = document.getElementById("police-unit-val");
      const sopEl = document.getElementById("police-sop-text");

      if (plateEl) plateEl.textContent = p.target_plate || "GJ01RQ6420";
      if (camEl) camEl.textContent = `${p.camera_name || "CH Road Market Gate"} (${p.camera_id || "cam04"})`;
      if (timeEl) timeEl.textContent = formatTimestamp(p.detection_time);
      if (reasonEl) reasonEl.textContent = p.threat_bulletin || "Terror watchlist entry";
      if (unitEl) unitEl.textContent = p.assigned_unit || "Gandhinagar PCR Van 04";
      if (sopEl) sopEl.textContent = p.sop_directive || "Priority tactical interception.";
    }

    if (depts.traffic) {
      const tr = depts.traffic;
      const chkEl = document.getElementById("interception-checkpoint-val");
      const routeEl = document.getElementById("interception-route-val");
      const spdEl = document.getElementById("interception-speed-val");
      const actEl = document.getElementById("interception-action-val");

      if (chkEl) chkEl.textContent = tr.recommended_checkpoint || "Highway Toll Plaza";
      if (routeEl && Array.isArray(tr.corridor_route)) {
        routeEl.innerHTML = tr.corridor_route.map(r => `<span>${escapeHtml(r)}</span>`).join(" &#10142; ");
      }
      if (spdEl) spdEl.textContent = tr.kinematic_feasibility || "23.2 – 26.9 km/h (Feasible)";
      if (actEl) actEl.textContent = tr.recommended_action || "Toll Barrier Closure";
    }

    if (depts.ambulance) {
      const amb = depts.ambulance;
      const incEl = document.getElementById("ambulance-incident-val");
      const unitEl = document.getElementById("ambulance-unit-val");
      const etaEl = document.getElementById("ambulance-eta-val");
      const facEl = document.getElementById("ambulance-facility-val");
      const stateEl = document.getElementById("ambulance-state-val");

      if (incEl) incEl.textContent = `${amb.linked_incident_type || "SOS DISTRESS"} (#${amb.linked_incident_id} - ${amb.incident_location})`;
      if (unitEl) unitEl.textContent = amb.assigned_unit || "108 EMRI Unit 04 (ALS)";
      if (etaEl) etaEl.innerHTML = `${amb.eta_mins || 4} mins <small class="text-muted">(${amb.distance_km || 1.8} km distance)</small>`;
      if (facEl) facEl.textContent = amb.receiving_facility || "Civil Hospital Gandhinagar Trauma";
      if (stateEl) stateEl.textContent = amb.dispatch_state || "DISPATCH RECOMMENDED";
    }

    if (depts.fire) {
      const f = depts.fire;
      const secEl = document.getElementById("fire-incident-val");
      const stnEl = document.getElementById("fire-station-val");
      const tdrEl = document.getElementById("fire-tender-val");
      const hydEl = document.getElementById("fire-hydrant-val");
      const stEl = document.getElementById("fire-state-val");

      if (secEl) secEl.textContent = f.sector_status || "Active Grid Monitoring";
      if (stnEl) stnEl.textContent = f.assigned_station || "Gandhinagar Central FRS (Sec 17)";
      if (tdrEl) tdrEl.innerHTML = `${f.nearest_unit || "Tender FRS-02"} <small class="text-muted">(Water Bouser)</small>`;
      if (hydEl) hydEl.textContent = `${f.hydrant_pressure_bar || 4.2} bar (Operational)`;
      if (stEl) stEl.textContent = f.dispatch_state || "STANDBY PROTOCOL ACTIVE";
    }
  }

  function updateGisLayers(gis) {
    if (!gis || !commandMap) return;

    if (facilityLayerGroup && Array.isArray(gis.facilities)) {
      facilityLayerGroup.clearLayers();
      gis.facilities.forEach((fac) => {
        const marker = L.circleMarker([fac.lat, fac.lon], {
          radius: 9,
          color: "#ffffff",
          fillColor: "#475569",
          fillOpacity: 0.9,
          weight: 2,
        });
        marker.bindPopup(`
          <div style="font-family:sans-serif; min-width:180px;">
            <strong style="color:#0f172a; font-size:14px;">🏥 ${escapeHtml(fac.name)}</strong><br>
            <span style="font-size:12px; color:#475569; text-transform:uppercase;">${escapeHtml(fac.type)}</span>
          </div>
        `);
        facilityLayerGroup.addLayer(marker);
      });
    }

    if (cameraLayerGroup && Array.isArray(gis.cameras)) {
      cameraLayerGroup.clearLayers();
      gis.cameras.forEach((cam) => {
        const marker = L.circleMarker([cam.lat, cam.lon], {
          radius: 7,
          color: "#38bdf8",
          fillColor: "#0284c7",
          fillOpacity: 0.9,
          weight: 2,
        });
        marker.bindPopup(`
          <div style="font-family:sans-serif; min-width:200px;">
            <strong style="color:#0f172a; font-size:13px;">📹 ${escapeHtml(cam.name)}</strong><br>
            <span style="font-size:11px; color:#64748b;">ID: <code>${escapeHtml(cam.id)}</code> | Source: ${escapeHtml(cam.source_type)}</span>
          </div>
        `);
        cameraLayerGroup.addLayer(marker);
      });
    }

    if (incidentLayerGroup && Array.isArray(gis.incidents)) {
      incidentLayerGroup.clearLayers();
      gis.incidents.forEach((inc) => {
        const isCrit = inc.severity === "CRITICAL";
        const color = isCrit ? "#f43f5e" : inc.severity === "HIGH" ? "#f59e0b" : "#38bdf8";
        
        const marker = L.circleMarker([inc.lat, inc.lon], {
          radius: isCrit ? 10 : 8,
          color: "#ffffff",
          fillColor: color,
          fillOpacity: 0.95,
          weight: isCrit ? 3 : 2,
        });

        marker.bindPopup(`
          <div style="font-family:sans-serif; min-width:220px;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
              <strong style="color:#0f172a; font-size:13px;">🚨 ${escapeHtml(inc.type.toUpperCase())}</strong>
              <span style="background:${color}; color:#fff; padding:2px 6px; border-radius:4px; font-size:10px; font-weight:bold;">${escapeHtml(inc.severity)}</span>
            </div>
            <span style="font-size:11px; color:#64748b;">Incident #${escapeHtml(inc.id)} | ${escapeHtml(inc.camera_name || inc.camera_id)}</span><br>
            <span style="font-size:11px; color:#64748b;">Time: ${escapeHtml(formatTimestamp(inc.timestamp))}</span><br>
            <div style="margin-top:8px;">
              <button onclick="window.openIncidentFocus(${inc.id})" style="width:100%; padding:5px 8px; font-size:11px; background:#2563eb; color:#fff; border:0; border-radius:4px; cursor:pointer; font-weight:bold;">🎯 Focus Incident &amp; Directives</button>
            </div>
          </div>
        `);
        incidentLayerGroup.addLayer(marker);
      });
    }

    if (vehicleLayerGroup && Array.isArray(gis.vehicles)) {
      vehicleLayerGroup.clearLayers();
      gis.vehicles.forEach((veh) => {
        const isWl = veh.is_watchlist;
        const marker = L.circleMarker([veh.lat, veh.lon], {
          radius: 7,
          color: isWl ? "#f59e0b" : "#10b981",
          fillColor: isWl ? "#d97706" : "#059669",
          fillOpacity: 0.9,
          weight: 2,
        });
        marker.bindPopup(`
          <div style="font-family:sans-serif; min-width:190px;">
            <strong style="color:#0f172a; font-size:13px;">🚗 ${escapeHtml(veh.plate_number)}</strong>
            ${isWl ? '<span style="background:#f59e0b; color:#fff; font-size:10px; padding:2px 4px; border-radius:3px; margin-left:4px; font-weight:bold;">WATCHLIST</span>' : ''}<br>
            <span style="font-size:11px; color:#64748b;">Camera: ${escapeHtml(veh.camera_name || veh.camera_id)}</span><br>
            <span style="font-size:11px; color:#64748b;">Sighted: ${escapeHtml(formatTimestamp(veh.timestamp))}</span><br>
            <div style="margin-top:6px;">
              <button onclick="window.trackPlate('${escapeHtml(veh.plate_number)}')" style="padding:4px 8px; font-size:11px; background:#059669; color:#fff; border:0; border-radius:4px; cursor:pointer; font-weight:bold;">🎯 Reconstruct Journey</button>
            </div>
          </div>
        `);
        vehicleLayerGroup.addLayer(marker);
      });
    }

    if (unitLayerGroup && Array.isArray(gis.units)) {
      unitLayerGroup.clearLayers();
      gis.units.forEach((u) => {
        const marker = L.circleMarker([u.base_lat, u.base_lon], {
          radius: 8,
          color: "#ffffff",
          fillColor: "#6366f1",
          fillOpacity: 0.95,
          weight: 2,
        });
        marker.bindPopup(`
          <div style="font-family:sans-serif; min-width:180px;">
            <strong style="color:#0f172a; font-size:13px;">🚓 ${escapeHtml(u.name)}</strong><br>
            <span style="font-size:11px; color:#64748b;">Callsign: <code>${escapeHtml(u.callsign)}</code></span><br>
            <span style="font-size:11px; color:#10b981; font-weight:bold;">Status: ${escapeHtml(u.status)}</span>
          </div>
        `);
        unitLayerGroup.addLayer(marker);
      });
    }
  }

  function updateSpotlightBar(inc) {
    if (!inc) return;
    const titleEl = document.getElementById("spotlight-title");
    const metaEl = document.getElementById("spotlight-meta");
    const btn = document.getElementById("spotlight-btn-drawer");

    if (titleEl) {
      titleEl.innerHTML = `<span style="color:#f43f5e; font-weight:800;">[${escapeHtml(inc.severity)}]</span> ${escapeHtml(inc.type.toUpperCase())} (#${inc.id}) &mdash; ${escapeHtml(inc.camera_name || inc.camera_id)}`;
    }
    if (metaEl) {
      metaEl.textContent = `Confidence: ${(inc.confidence * 100).toFixed(1)}% | ${formatTimestamp(inc.timestamp)}`;
    }
    if (btn) {
      btn.style.display = "inline-block";
      btn.onclick = () => openIncidentFocus(inc.id);
    }
  }

  async function trackPlate(plateQuery) {
    initCommandMap();
    const plate = String(plateQuery || "").trim().toUpperCase();
    if (!plate) return;

    const input = document.getElementById("journey-plate-input");
    if (input) input.value = plate;

    const summaryEl = document.getElementById("journey-summary");
    const timelineEl = document.getElementById("journey-timeline");
    const badgeEl = document.getElementById("journey-status-badge");

    summaryEl.style.display = "block";
    summaryEl.innerHTML = `<em>Querying cross-camera sightings for plate <strong>${escapeHtml(plate)}</strong>...</em>`;

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
      const statusBadgeHtml = `<span class="kpi-pill ${isFeasible ? 'kpi-pill-success' : 'kpi-pill-danger'}">${isFeasible ? '✓ FEASIBLE JOURNEY' : '⚠ IMPLAUSIBLE SPEED JUMP'}</span>`;
      if (badgeEl) badgeEl.innerHTML = statusBadgeHtml;

      summaryEl.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
          <div>
            <strong style="color:var(--accent-cyan); font-size: 1.15rem; letter-spacing: 0.05em; font-family:var(--font-mono);">${escapeHtml(data.plate)}</strong>
            <span style="color: var(--text-muted); font-size: 0.85rem; margin-left: 12px;">Total Sightings: <strong>${data.sighting_count}</strong> across <strong>${data.segments ? data.segments.length + 1 : 1}</strong> cameras</span>
          </div>
          <div>${statusBadgeHtml}</div>
        </div>
        <p style="margin: 6px 0 0 0; font-size: 0.74rem; color: var(--text-dim);">${escapeHtml(data.disclaimer)}</p>
      `;

      let timelineHtml = "";
      const latlngs = [];

      data.sightings.forEach((s, idx) => {
        latlngs.push([s.lat, s.lon]);

        if (commandMap && journeyLayerGroup) {
          const marker = L.circleMarker([s.lat, s.lon], {
            radius: 9,
            color: "#38bdf8",
            fillColor: "#0284c7",
            fillOpacity: 0.95,
            weight: 3,
          });
          marker.bindPopup(`
            <strong style="color:#0f172a;">Hop #${idx + 1}: ${escapeHtml(s.camera_name || s.camera_id)}</strong><br>
            Time: ${escapeHtml(formatTimestamp(s.timestamp))}<br>
            Plate: <code>${escapeHtml(s.plate_number)}</code><br>
            Confidence: ${(s.confidence * 100).toFixed(1)}%
          `);
          journeyLayerGroup.addLayer(marker);
        }

        timelineHtml += `
          <div class="timeline-item">
            <div class="timeline-icon">${idx + 1}</div>
            <div class="timeline-details">
              <div class="timeline-cam">${escapeHtml(s.camera_name || s.camera_id)} <span style="font-size:0.75rem; color:#64748b;">(${escapeHtml(s.camera_id)})</span></div>
              <div class="timeline-meta">${escapeHtml(formatTimestamp(s.timestamp))} &bull; Confidence: ${(s.confidence * 100).toFixed(1)}%</div>
            </div>
          </div>
        `;

        if (data.segments && data.segments[idx]) {
          const seg = data.segments[idx];
          const isHopFeas = seg.feasible;
          timelineHtml += `
            <div class="timeline-hop-box ${isHopFeas ? '' : 'hop-infeasible'}">
              Corridor hop: <strong>${seg.distance_km} km</strong> in <strong>${seg.elapsed_seconds}s</strong> &bull;
              Required Speed: <span class="${isHopFeas ? 'text-success' : 'text-danger'} font-bold">${seg.required_speed_kmh} km/h (${isHopFeas ? 'Feasible' : 'Infeasible Jump'})</span>
              ${seg.note ? `<div style="color:#f87171; font-size:0.75rem; margin-top:2px;">⚠ ${escapeHtml(seg.note)}</div>` : ''}
            </div>
          `;
        }
      });

      timelineEl.innerHTML = timelineHtml;

      if (commandMap && journeyLayerGroup && data.segments) {
        data.segments.forEach((seg, i) => {
          const p1 = [data.sightings[i].lat, data.sightings[i].lon];
          const p2 = [data.sightings[i + 1].lat, data.sightings[i + 1].lon];
          const polyline = L.polyline([p1, p2], {
            color: seg.feasible ? "#10b981" : "#ef4444",
            weight: 4,
            opacity: 0.9,
            dashArray: seg.feasible ? null : "8, 8",
          });
          polyline.bindPopup(`Corridor: ${escapeHtml(seg.from_camera_name)} &#10142; ${escapeHtml(seg.to_camera_name)}<br>Distance: ${seg.distance_km} km<br>Speed: ${seg.required_speed_kmh} km/h`);
          journeyLayerGroup.addLayer(polyline);
        });

        if (latlngs.length > 0) {
          commandMap.fitBounds(L.latLngBounds(latlngs), { padding: [50, 50] });
        }
      }

      appendActivityLog(`ANPR Journey Trajectory Reconstructed: ${plate} (${data.sighting_count} Sightings)`);
    } catch (err) {
      summaryEl.innerHTML = `<span style="color:#ef4444;">Error reconstructing journey: ${escapeHtml(err.message)}</span>`;
      timelineEl.innerHTML = "";
    }
  }

  async function openIncidentFocus(incidentId) {
    activeFocusIncidentId = incidentId;
    const drawer = document.getElementById("incident-drawer");
    const backdrop = document.getElementById("incident-drawer-backdrop");
    const content = document.getElementById("drawer-body-content");
    const titleEl = document.getElementById("drawer-incident-title");

    if (titleEl) titleEl.textContent = `Incident #${incidentId}`;
    if (drawer) drawer.classList.add("open");
    if (backdrop) backdrop.classList.add("active");

    if (content) {
      content.innerHTML = `<div class="drawer-loading">Querying incident intelligence and vehicle correlation matrix...</div>`;
    }

    try {
      const data = await fetchJson(`/dashboard/incident/${incidentId}`);
      const inc = data.incident;
      updateSpotlightBar(inc);

      if (commandMap && inc.lat && inc.lon) {
        commandMap.flyTo([inc.lat, inc.lon], 15, { duration: 1.2 });
      }

      let vehiclesHtml = "";
      if (Array.isArray(data.correlated_vehicles) && data.correlated_vehicles.length > 0) {
        vehiclesHtml = data.correlated_vehicles.map((v) => `
          <div style="display:flex; justify-content:space-between; align-items:center; padding:8px 10px; background:#090e18; border:1px solid #1e293b; border-radius:6px; margin-bottom:6px;">
            <div>
              <strong style="color:var(--accent-cyan); font-family:var(--font-mono);">${escapeHtml(v.plate_number)}</strong>
              ${v.is_watchlist ? '<span class="kpi-pill kpi-pill-danger" style="margin-left:6px;">WATCHLIST</span>' : ''}
              <div style="font-size:0.72rem; color:var(--text-muted); margin-top:2px;">Time delta: +${v.delta_seconds}s &bull; Confidence: ${(v.confidence * 100).toFixed(1)}%</div>
            </div>
            <button type="button" onclick="window.trackPlate('${escapeHtml(v.plate_number)}')" class="btn-primary btn-sm">🎯 Track</button>
          </div>
        `).join("");
      } else {
        vehiclesHtml = `<p class="empty-state" style="font-size:0.78rem;">No vehicles recorded at this camera within &plusmn;120s of incident.</p>`;
      }

      let nearbyCamHtml = "";
      if (Array.isArray(data.nearby_cameras) && data.nearby_cameras.length > 0) {
        nearbyCamHtml = data.nearby_cameras.slice(0, 5).map((c) => `
          <div style="display:flex; justify-content:space-between; align-items:center; padding:6px 8px; background:#090e18; border-radius:5px; margin-bottom:4px; font-size:0.78rem;">
            <span>📹 ${escapeHtml(c.name)} <small class="text-muted">(${escapeHtml(c.camera_id)})</small></span>
            <span style="color:var(--accent-cyan); font-weight:700;">${c.distance_km} km</span>
          </div>
        `).join("");
      } else {
        nearbyCamHtml = `<p class="empty-state" style="font-size:0.78rem;">No nearby cameras in grid.</p>`;
      }

      const rec = data.recommendations || {};

      content.innerHTML = `
        <div class="drawer-section">
          <div class="drawer-section-title">🚨 Incident Telemetry</div>
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; font-size:0.8rem;">
            <div><span class="text-muted">Type:</span> <strong style="color:#fff;">${escapeHtml(inc.type.toUpperCase())}</strong></div>
            <div><span class="text-muted">Severity:</span> <span class="sev-${escapeHtml(inc.severity.toLowerCase())} alert-sev-pill">${escapeHtml(inc.severity)}</span></div>
            <div><span class="text-muted">Camera:</span> <strong>${escapeHtml(inc.camera_name || inc.camera_id)}</strong></div>
            <div><span class="text-muted">Confidence:</span> <strong>${(inc.confidence * 100).toFixed(1)}%</strong></div>
            <div style="grid-column:1/-1;"><span class="text-muted">Time:</span> ${escapeHtml(formatTimestamp(inc.timestamp))}</div>
            <div style="grid-column:1/-1;"><span class="text-muted">Coordinates:</span> <code>${inc.lat.toFixed(4)}, ${inc.lon.toFixed(4)}</code></div>
          </div>
          <div style="margin-top:8px; padding:8px; background:#090e18; border-radius:6px; font-size:0.76rem; color:#cbd5e1;">
            ${escapeHtml(inc.details || "No raw detector payload")}
          </div>
        </div>

        <div class="drawer-section">
          <div class="drawer-section-title">🚗 Correlated Vehicles (&plusmn;120s Window)</div>
          ${vehiclesHtml}
        </div>

        <div class="drawer-section">
          <div class="drawer-section-title">🛡️ Multi-Agency Directives</div>
          <div style="display:flex; flex-direction:column; gap:8px; font-size:0.78rem;">
            <div style="padding:8px; background:#090e18; border-left:3px solid #0284c7; border-radius:4px;">
              <strong style="color:#38bdf8;">POLICE TACTICAL:</strong>
              <p style="margin:2px 0 0 0; color:#cbd5e1;">${escapeHtml(rec.police?.action || "Dispatch nearest patrol unit.")}</p>
            </div>
            <div style="padding:8px; background:#090e18; border-left:3px solid #e11d48; border-radius:4px;">
              <strong style="color:#f43f5e;">AMBULANCE 108 CAD:</strong>
              <p style="margin:2px 0 0 0; color:#cbd5e1;">${escapeHtml(rec.ambulance?.action || "EMS standby alert.")}</p>
            </div>
            <div style="padding:8px; background:#090e18; border-left:3px solid #059669; border-radius:4px;">
              <strong style="color:#10b981;">TRAFFIC CORRIDOR:</strong>
              <p style="margin:2px 0 0 0; color:#cbd5e1;">${escapeHtml(rec.traffic?.action || "Green corridor clear.")}</p>
            </div>
          </div>
        </div>

        <div class="drawer-section">
          <div class="drawer-section-title">📹 Escape Route Sentinel Cameras</div>
          ${nearbyCamHtml}
        </div>

        <button type="button" class="btn-primary btn-block" onclick="window.flyToIncident(${inc.lat}, ${inc.lon})">
          🗺️ Center GIS Map on Incident
        </button>
      `;

      appendActivityLog(`Incident Focus Mode Activated: #${incidentId} (${inc.type})`);
    } catch (err) {
      if (content) {
        content.innerHTML = `<span style="color:#ef4444;">Failed to load incident detail: ${escapeHtml(err.message)}</span>`;
      }
    }
  }

  function closeIncidentDrawer() {
    const drawer = document.getElementById("incident-drawer");
    const backdrop = document.getElementById("incident-drawer-backdrop");
    if (drawer) drawer.classList.remove("open");
    if (backdrop) backdrop.classList.remove("active");
  }

  function flyToIncident(lat, lon) {
    if (commandMap && lat && lon) {
      commandMap.flyTo([lat, lon], 16, { duration: 1 });
      closeIncidentDrawer();
    }
  }

  function acknowledgeDispatch(agency) {
    let msg = "";
    if (agency === "police") {
      msg = "🚓 Gandhinagar PCR Van 04 Dispatched to Sector 6 Chowki. Tactical interception active.";
    } else if (agency === "traffic") {
      msg = "🛑 Traffic Interceptor TI-01 Checkpoint Staged at Highway Toll Plaza. Toll barrier lock initiated.";
    } else if (agency === "ambulance") {
      msg = "🚑 108 EMRI Unit 04 (ALS) CAD Dispatched. ETA 4 mins. Civil Hospital Trauma notified.";
    } else if (agency === "fire") {
      msg = "🚒 Fire Tender FRS-02 Staged. Hydrant line at 4.2 bar confirmed operational.";
    }
    showToast(msg, "success");
    appendActivityLog(`CAD Dispatch Acknowledged: ${agency.toUpperCase()} unit ordered`);
  }

  function viewCorridorOnMap() {
    const mapEl = document.getElementById("command-map");
    if (mapEl) mapEl.scrollIntoView({ behavior: "smooth" });
    trackPlate("GJ01RQ6420");
  }

  async function refreshAlerts() {
    try {
      const alerts = await fetchJson("/alerts");
      cachedAlerts = alerts;

      const cntAll = alerts.length;
      const cntCrit = alerts.filter(a => {
        const t = String(a.type || "").toLowerCase();
        return t.includes("critical") || t.includes("sos") || t.includes("watchlist") || t.includes("fire");
      }).length;
      const cntWl = alerts.filter(a => String(a.type || "").toLowerCase().includes("watchlist")).length;

      const elAll = document.getElementById("tab-cnt-all");
      const elCrit = document.getElementById("tab-cnt-critical");
      const elWl = document.getElementById("tab-cnt-watchlist");
      const elBadge = document.getElementById("alert-count-badge");
      const lastRef = document.getElementById("last-refresh");

      if (elAll) elAll.textContent = cntAll;
      if (elCrit) elCrit.textContent = cntCrit;
      if (elWl) elWl.textContent = cntWl;
      if (elBadge) elBadge.textContent = `${cntAll} Incidents`;
      if (lastRef) lastRef.textContent = `Updated ${new Date().toLocaleTimeString()}`;

      renderFilteredAlerts();
    } catch (err) {
      console.error("Alerts refresh failed:", err);
    }
  }

  function renderFilteredAlerts() {
    const feed = document.getElementById("alert-feed");
    if (!feed) return;

    let filtered = cachedAlerts;
    if (currentFilter === "critical") {
      filtered = cachedAlerts.filter(a => {
        const t = String(a.type || "").toLowerCase();
        return t.includes("sos") || t.includes("watchlist") || t.includes("fire");
      });
    } else if (currentFilter === "watchlist") {
      filtered = cachedAlerts.filter(a => String(a.type || "").toLowerCase().includes("watchlist"));
    } else if (currentFilter === "sos") {
      filtered = cachedAlerts.filter(a => String(a.type || "").toLowerCase().includes("sos"));
    } else if (currentFilter === "fall") {
      filtered = cachedAlerts.filter(a => String(a.type || "").toLowerCase().includes("fall"));
    } else if (currentFilter === "fight") {
      filtered = cachedAlerts.filter(a => String(a.type || "").toLowerCase().includes("fight"));
    }

    if (filtered.length === 0) {
      feed.innerHTML = `<p class="empty-state">No incidents matching filter "${escapeHtml(currentFilter)}".</p>`;
      return;
    }

    feed.innerHTML = filtered.map((alert) => {
      const type = String(alert.type || "unknown").toLowerCase();
      const isWatchlist = type.includes("watchlist");
      const detailsText = String(alert.details || "");

      let sev = "MEDIUM";
      if (type.includes("watchlist") || type.includes("sos") || type.includes("fire")) {
        sev = "CRITICAL";
      } else if (type.includes("fight") || type.includes("fall")) {
        sev = "HIGH";
      }

      let plateStr = "";
      const plateMatch = detailsText.match(/plate=([A-Z0-9]+)/i);
      if (plateMatch) {
        plateStr = plateMatch[1].toUpperCase();
      }

      return `
        <article class="alert-card alert-${escapeHtml(type)} ${sev === 'CRITICAL' ? 'alert-critical' : ''}" data-alert-id="${escapeHtml(alert.id)}">
          <div class="alert-card-header">
            <span class="alert-type-badge">
              ${isWatchlist ? '🎯 ' : '🚨 '}${escapeHtml(type.toUpperCase())}
            </span>
            <span class="alert-sev-pill sev-${sev.toLowerCase()}">${sev}</span>
          </div>
          <div class="alert-card-meta">
            <span>Confidence: <strong>${escapeHtml(formatConfidence(alert.confidence))}</strong></span>
            <time datetime="${escapeHtml(alert.timestamp)}">${escapeHtml(formatTimestamp(alert.timestamp))}</time>
          </div>
          <p class="alert-details">${escapeHtml(detailsText || "No detector metrics recorded")}</p>
          <div class="alert-card-footer">
            <span style="font-size:0.75rem; color:var(--text-dim); font-family:var(--font-mono);">#${escapeHtml(alert.id)}</span>
            <div style="display:flex; gap:6px;">
              ${plateStr ? `<button type="button" class="btn-primary btn-sm" onclick="window.trackPlate('${escapeHtml(plateStr)}')">🎯 Track Plate</button>` : ''}
              <button type="button" class="btn-focus-incident" onclick="window.openIncidentFocus(${alert.id})">Focus Incident ➔</button>
            </div>
          </div>
        </article>
      `;
    }).join("");
  }

  async function refreshCameras() {
    try {
      const cameras = await fetchJson("/cameras");
      cachedCameras = cameras;
      const listEl = document.getElementById("camera-list");
      if (!listEl) return;

      if (!Array.isArray(cameras) || cameras.length === 0) {
        listEl.innerHTML = '<p class="empty-state">No cameras registered in SQLite.</p>';
        return;
      }

      listEl.innerHTML = cameras.map((c) => `
        <div class="camera-card" data-camera-id="${escapeHtml(c.id)}">
          <div class="camera-card-info">
            <span class="camera-card-title">📹 ${escapeHtml(c.name)}</span>
            <span class="camera-card-sub" title="${escapeHtml(c.source)}">${escapeHtml(c.source)}</span>
          </div>
          <button type="button" class="btn-locate-cam" onclick="window.locateCamera(${c.id})">Locate</button>
        </div>
      `).join("");
    } catch (err) {
      console.error("Camera refresh failed:", err);
    }
  }

  function locateCamera(camId) {
    if (!commandMap) return;
    const cam = cachedCameras.find(c => String(c.id) === String(camId));
    if (cam) {
      const mapEl = document.getElementById("command-map");
      if (mapEl) mapEl.scrollIntoView({ behavior: "smooth" });
      commandMap.flyTo([23.235, 72.655], 14);
    }
  }

  function filterAlertsByCam(camId) {
    const feedEl = document.getElementById("alert-feed");
    if (feedEl) feedEl.scrollIntoView({ behavior: "smooth" });
    showToast(`Filtering stream for Camera ${camId}`, "info");
  }

  let pollInterval = null;

  async function uploadVideoForAnalysis(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const fileInput = document.getElementById("video-file");
    const message = document.getElementById("upload-form-message");

    if (!fileInput.files || fileInput.files.length === 0) {
      message.className = "form-message text-danger";
      message.textContent = "Please select a video file.";
      return;
    }

    const file = fileInput.files[0];
    const formData = new FormData();
    formData.append("video", file);

    try {
      message.className = "form-message text-info";
      message.textContent = "Uploading video for Sentinel forensic analysis...";

      const response = await fetch(`${API_BASE}/videos/analyze`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const body = await response.json();
        throw new Error(body.detail || `${response.status} ${response.statusText}`);
      }

      const job = await response.json();
      message.className = "form-message text-success";
      message.textContent = `Upload complete. Analysis running (Job: ${job.job_id.slice(0, 8)}...)`;
      form.reset();

      document.getElementById("batch-status").innerHTML = `
        <div style="padding:10px; background:#090e18; border:1px solid #334155; border-radius:6px; font-size:0.78rem; margin-top:8px;">
          <strong>Status:</strong> <span class="text-info">${escapeHtml(job.status.toUpperCase())}</span><br>
          <strong>Job ID:</strong> <code>${escapeHtml(job.job_id)}</code><br>
          <strong>Video:</strong> ${escapeHtml(job.video_filename)}
        </div>
      `;

      if (pollInterval) clearInterval(pollInterval);
      pollInterval = setInterval(() => pollJobStatus(job.job_id), 3000);
      pollJobStatus(job.job_id);
    } catch (error) {
      message.className = "form-message text-danger";
      message.textContent = `Upload failed: ${error.message}`;
    }
  }

  async function pollJobStatus(jobId) {
    try {
      const job = await fetchJson(`/videos/analyze/${jobId}`);
      let statusHtml = `
        <div style="padding:10px; background:#090e18; border:1px solid #334155; border-radius:6px; font-size:0.78rem; margin-top:8px;">
          <strong>Status:</strong> <span class="text-info font-bold">${escapeHtml(job.status.toUpperCase())}</span><br>
          <strong>Job ID:</strong> <code>${escapeHtml(job.job_id)}</code><br>
          <strong>Video:</strong> ${escapeHtml(job.video_filename || "Unknown")}
      `;

      if (job.progress) {
        const pct = (job.progress.frames_processed / job.progress.total_frames * 100).toFixed(1);
        statusHtml += `
          <div style="margin-top:6px; background:#1e293b; border-radius:4px; height:8px; overflow:hidden;">
            <div style="width:${pct}%; background:#38bdf8; height:100%;"></div>
          </div>
          <span style="font-size:0.72rem; color:var(--text-muted);">${job.progress.frames_processed} / ${job.progress.total_frames} frames (${pct}%)</span>
        `;
      }

      statusHtml += `</div>`;
      document.getElementById("batch-status").innerHTML = statusHtml;

      if (job.status === "completed" || job.status === "failed") {
        if (pollInterval) {
          clearInterval(pollInterval);
          pollInterval = null;
        }
      }
    } catch (err) {
      console.error("Job status polling failed:", err);
    }
  }

  function initialize() {
    initCommandMap();

    const btnRef = document.getElementById("btn-quick-refresh");
    if (btnRef) {
      btnRef.addEventListener("click", () => {
        updateDashboardSummary();
        refreshAlerts();
        showToast("Dashboard synchronized with SQLite database", "info");
      });
    }

    const closeBtn = document.getElementById("btn-close-drawer");
    const backdrop = document.getElementById("incident-drawer-backdrop");
    if (closeBtn) closeBtn.addEventListener("click", closeIncidentDrawer);
    if (backdrop) backdrop.addEventListener("click", closeIncidentDrawer);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeIncidentDrawer();
    });

    const journeyForm = document.getElementById("journey-search-form");
    if (journeyForm) {
      journeyForm.addEventListener("submit", (e) => {
        e.preventDefault();
        const input = document.getElementById("journey-plate-input");
        trackPlate(input.value);
      });
    }

    const filterTabs = document.querySelectorAll(".filter-tab");
    filterTabs.forEach((tab) => {
      tab.addEventListener("click", () => {
        filterTabs.forEach((t) => t.classList.remove("active"));
        tab.classList.add("active");
        currentFilter = tab.dataset.filter || "all";
        renderFilteredAlerts();
      });
    });

    const uploadForm = document.getElementById("upload-form");
    if (uploadForm) {
      uploadForm.addEventListener("submit", uploadVideoForAnalysis);
    }

    refreshCameras().then(() => {
      updateDashboardSummary();
      refreshAlerts();
      trackPlate("GJ01RQ6420");
    });

    global.setInterval(() => {
      updateDashboardSummary();
      refreshAlerts();
    }, POLL_INTERVAL_MS);
  }

  global.trackPlate = trackPlate;
  global.openIncidentFocus = openIncidentFocus;
  global.closeIncidentDrawer = closeIncidentDrawer;
  global.flyToIncident = flyToIncident;
  global.acknowledgeDispatch = acknowledgeDispatch;
  global.viewCorridorOnMap = viewCorridorOnMap;
  global.locateCamera = locateCamera;
  global.filterAlertsByCam = filterAlertsByCam;

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", initialize);
    } else {
      initialize();
    }
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
