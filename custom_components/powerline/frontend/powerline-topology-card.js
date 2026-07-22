/* Powerline Topology Card
 *
 * Renders the mesh graph served by the powerline integration's
 * `powerline/topology` websocket command: adapters as nodes, PLC links as
 * edges coloured by link quality. Clicking a node or edge shows details.
 *
 * Lovelace config:
 *   type: custom:powerline-topology-card
 *   title: Powerline Mesh            # optional
 *   entry_id: <config entry id>      # optional, only for multiple entries
 *   refresh_interval: 30             # optional, seconds
 */

(() => {
  "use strict";

  const QUALITY_COLORS = {
    green: "#43a047",
    yellow: "#fdd835",
    orange: "#fb8c00",
    red: "#e53935",
    unknown: "#9e9e9e",
  };

  // All user-facing strings, keyed by language. The card follows Home
  // Assistant's UI language (this._hass.language): German when it starts with
  // "de", English otherwise. Plain-language role/quality labels replace the
  // raw HomePlug acronyms and colour keys returned by the backend.
  const STRINGS = {
    de: {
      role_CCo: "Zentrale (Koordinator)",
      role_Station: "Teilnehmer",
      role_unknown: "unbekannt",
      badge_CCo: "Zentrale",
      quality_green: "sehr gut",
      quality_yellow: "gut",
      quality_orange: "mittelmäßig",
      quality_red: "schlecht",
      quality_unknown: "unbekannt",
      legend_estimated: "gestrichelt = geschätzt",
      history_loading: "Lade Verlauf …",
      history_empty: "Noch keine Verlaufsdaten für diesen Zeitraum.",
      hint_click: "Adapter oder Verbindung anklicken für Details.",
      empty: "Noch keine Powerline-Adapter erkannt.",
      name: "Name",
      mac: "MAC",
      model: "Modell",
      firmware: "Firmware",
      manufacturer: "Hersteller",
      chipset: "Chipsatz",
      role: "Rolle",
      status: "Status",
      online: "online",
      offline: "offline",
      last_update: "Letztes Update",
      connection: "Verbindung",
      average: "Durchschnitt",
      quality: "Qualität",
      note: "Hinweis",
      note_estimated: "geschätzt (keine paarweise Messung)",
      analysis_worst: "Schwächste",
      analysis_unstable: "Instabilste",
      analysis_offline: "Offline",
      settings: "Einstellungen",
      info: "Info:",
      run: "Ausführen",
      range_168: "7 T",
      range_720: "30 T",
      outage: "Aussetzer – Adapter war zeitweise offline (rot markiert)",
      edit: "Anordnen",
      edit_done: "Fertig",
      bg_upload: "Hintergrund",
      bg_remove: "Hintergrund entfernen",
      reset_positions: "Auto-Anordnung",
      edit_hint: "Adapter mit der Maus/Finger an ihre Position im Grundriss ziehen.",
      bg_too_large: "Bild zu groß (max. 4 MB). Bitte ein kleineres verwenden.",
      layout_saved: "Anordnung gespeichert",
      icon_size: "Symbolgröße",
      icon_style: "Symbolstil",
    },
    en: {
      role_CCo: "Coordinator",
      role_Station: "Station",
      role_unknown: "unknown",
      badge_CCo: "Coordinator",
      quality_green: "very good",
      quality_yellow: "good",
      quality_orange: "fair",
      quality_red: "poor",
      quality_unknown: "unknown",
      legend_estimated: "dashed = estimated",
      history_loading: "Loading history …",
      history_empty: "No history data for this range yet.",
      hint_click: "Click an adapter or connection for details.",
      empty: "No powerline adapters discovered yet.",
      name: "Name",
      mac: "MAC",
      model: "Model",
      firmware: "Firmware",
      manufacturer: "Manufacturer",
      chipset: "Chipset",
      role: "Role",
      status: "Status",
      online: "online",
      offline: "offline",
      last_update: "Last update",
      connection: "Connection",
      average: "Average",
      quality: "Quality",
      note: "Note",
      note_estimated: "estimated (no pairwise measurement)",
      analysis_worst: "Weakest",
      analysis_unstable: "Most unstable",
      analysis_offline: "Offline",
      settings: "Settings",
      info: "Info:",
      run: "Run",
      range_168: "7 d",
      range_720: "30 d",
      outage: "Outage – adapter was offline for a while (marked red)",
      edit: "Arrange",
      edit_done: "Done",
      bg_upload: "Background",
      bg_remove: "Remove background",
      reset_positions: "Auto layout",
      edit_hint: "Drag adapters to where they sit in your floor plan.",
      bg_too_large: "Image too large (max 4 MB). Please use a smaller one.",
      layout_saved: "Layout saved",
      icon_size: "Icon size",
      icon_style: "Icon style",
    },
  };

  const VIEW_W = 600;
  const VIEW_H = 400;
  const ADAPTER_ASSETS = {
    on:
      "data:image/png;base64," +
      "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" +
      "M4vuAQAAAABJRU5ErkJggg==",
    off:
      "data:image/png;base64," +
      "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",
  };

  class PowerlineTopologyCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._topology = null;
      this._positions = {}; // mac -> {x, y}
      this._selected = null; // {kind: "node"|"edge", id}
      this._history = null; // {key, hours, series}
      this._historyHours = 24;
      this._historyLoading = false;
      this._sparkMeta = null; // scale info for sparkline hover readout
      this._timer = null;
      this._lastFetch = 0;
      this._fetchInFlight = false;
      this._error = null;
      // User-arranged layout (server-persisted): manual adapter positions and
      // an optional floor-plan background image.
      this._userLayout = { positions: {}, background: null, iconScale: 1, iconStyle: "chatgpt" };
      this._layoutLoaded = false;
      this._editing = false; // "Arrange" mode: drag adapters, set background
      this._drag = null; // active drag {mac, moved}
      this._toast = null; // transient status message
    }

    setConfig(config) {
      this._config = {
        // title: "" hides the header entirely (used by the full-page panel)
        title: config.title === undefined ? "Powerline Mesh" : config.title,
        entry_id: config.entry_id || undefined,
        refresh_interval: Math.max(5, Number(config.refresh_interval) || 30),
      };
    }

    static getStubConfig() {
      return { title: "Powerline Mesh" };
    }

    getCardSize() {
      return 5;
    }

    set hass(hass) {
      this._hass = hass;
      if (!this._config) return;
      // hass updates arrive on every state change; only refetch on our own
      // schedule so we don't hammer the websocket.
      const now = Date.now();
      if (now - this._lastFetch > this._config.refresh_interval * 1000) {
        this._fetch();
      }
      if (!this._timer) {
        this._timer = setInterval(
          () => this._fetch(),
          this._config.refresh_interval * 1000
        );
      }
    }

    connectedCallback() {
      if (this._hass) this._fetch();
    }

    disconnectedCallback() {
      if (this._timer) {
        clearInterval(this._timer);
        this._timer = null;
      }
    }

    async _fetch() {
      if (!this._hass || this._fetchInFlight) return;
      if (!this._layoutLoaded) await this._fetchLayout();
      this._fetchInFlight = true;
      this._lastFetch = Date.now();
      const msg = { type: "powerline/topology" };
      if (this._config.entry_id) msg.entry_id = this._config.entry_id;
      try {
        const topology = await this._hass.callWS(msg);
        this._error = null;
        this._setTopology(topology);
      } catch (err) {
        this._error = (err && err.message) || "topology request failed";
        this._render();
      } finally {
        this._fetchInFlight = false;
      }
    }

    async _fetchLayout() {
      this._layoutLoaded = true;
      const msg = { type: "powerline/topology/layout/get" };
      if (this._config.entry_id) msg.entry_id = this._config.entry_id;
      try {
        const layout = await this._hass.callWS(msg);
        this._userLayout = {
          positions: (layout && layout.positions) || {},
          background: (layout && layout.background) || null,
          iconScale: (layout && Number(layout.icon_scale)) || 1,
          iconStyle: (layout && layout.icon_style) === "claude" ? "claude" : "chatgpt",
        };
      } catch (err) {
        this._userLayout = { positions: {}, background: null, iconScale: 1, iconStyle: "chatgpt" };
      }
    }

    // Persist the current layout. `patch` carries only what changed
    // (positions and/or background) so a drag doesn't re-upload the image.
    async _saveLayout(patch) {
      const msg = { type: "powerline/topology/layout/set", ...patch };
      if (this._config.entry_id) msg.entry_id = this._config.entry_id;
      try {
        await this._hass.callWS(msg);
        this._showToast(this._t("layout_saved"));
      } catch (err) {
        this._showToast((err && err.message) || "save failed");
      }
    }

    _showToast(text) {
      this._toast = text;
      this._render();
      clearTimeout(this._toastTimer);
      this._toastTimer = setTimeout(() => {
        this._toast = null;
        this._render();
      }, 2500);
    }

    async _fetchHistory(edge, hours) {
      if (!this._hass) return;
      const key = `${edge.source}|${edge.destination}`;
      this._historyHours = hours;
      this._historyLoading = true;
      this._render();
      try {
        const result = await this._hass.callWS({
          type: "powerline/topology/history",
          source: edge.source,
          destination: edge.destination,
          hours,
        });
        this._history = { key, hours, series: result.series || [] };
      } catch (err) {
        this._history = { key, hours, series: [] };
      } finally {
        this._historyLoading = false;
        this._render();
      }
    }

    _setTopology(topology) {
      const oldMacs = this._topology
        ? this._topology.nodes.map((n) => n.mac).join()
        : "";
      const newMacs = topology.nodes.map((n) => n.mac).join();
      this._topology = topology;
      if (oldMacs !== newMacs) this._layout();
      // A background refresh must never yank the graph out from under an
      // in-progress drag or the arrange toolbar.
      if (this._drag) return;
      this._render();
    }

    // Overlay user-placed positions (from the saved layout) onto the
    // auto-computed ones, so manually arranged adapters stay put while any
    // newly discovered adapter still gets a sensible automatic spot.
    _applyLayoutOverrides() {
      const saved = (this._userLayout && this._userLayout.positions) || {};
      Object.entries(saved).forEach(([mac, p]) => {
        if (p && typeof p.x === "number" && typeof p.y === "number") {
          this._positions[mac] = { x: p.x, y: p.y };
        }
      });
    }

    // ── Force-directed layout ──────────────────────────────
    // Small graphs (2-10 adapters): a few hundred iterations of
    // repulsion + edge springs + centering converge instantly.
    _layout() {
      const nodes = this._topology.nodes;
      const edges = this._topology.edges;
      const n = nodes.length;
      const pos = {};
      // Deterministic start: circle, CCo first so it tends to the middle
      const sorted = [...nodes].sort((a, b) =>
        a.role === "CCo" ? -1 : b.role === "CCo" ? 1 : 0
      );
      sorted.forEach((node, i) => {
        const angle = (2 * Math.PI * i) / Math.max(1, n);
        const r = i === 0 && node.role === "CCo" ? 0 : 130;
        pos[node.mac] = {
          x: VIEW_W / 2 + r * Math.cos(angle),
          y: VIEW_H / 2 + r * Math.sin(angle),
        };
      });
      const macs = nodes.map((d) => d.mac);
      for (let iter = 0; iter < 300; iter++) {
        const force = {};
        macs.forEach((m) => (force[m] = { x: 0, y: 0 }));
        // Repulsion between every pair
        for (let i = 0; i < macs.length; i++) {
          for (let j = i + 1; j < macs.length; j++) {
            const a = pos[macs[i]];
            const b = pos[macs[j]];
            let dx = a.x - b.x;
            let dy = a.y - b.y;
            let d2 = dx * dx + dy * dy;
            if (d2 < 1) {
              dx = (Math.random() - 0.5) * 2;
              dy = (Math.random() - 0.5) * 2;
              d2 = dx * dx + dy * dy;
            }
            const f = 12000 / d2;
            const d = Math.sqrt(d2);
            force[macs[i]].x += (dx / d) * f;
            force[macs[i]].y += (dy / d) * f;
            force[macs[j]].x -= (dx / d) * f;
            force[macs[j]].y -= (dy / d) * f;
          }
        }
        // Springs along edges (target length 150)
        edges.forEach((e) => {
          const a = pos[e.source];
          const b = pos[e.destination];
          if (!a || !b) return;
          const dx = b.x - a.x;
          const dy = b.y - a.y;
          const d = Math.max(1, Math.sqrt(dx * dx + dy * dy));
          const f = 0.02 * (d - 150);
          force[e.source].x += (dx / d) * f;
          force[e.source].y += (dy / d) * f;
          force[e.destination].x -= (dx / d) * f;
          force[e.destination].y -= (dy / d) * f;
        });
        // Gentle pull to the centre
        macs.forEach((m) => {
          force[m].x += (VIEW_W / 2 - pos[m].x) * 0.01;
          force[m].y += (VIEW_H / 2 - pos[m].y) * 0.01;
        });
        const cool = 1 - iter / 300;
        macs.forEach((m) => {
          pos[m].x += Math.max(-8, Math.min(8, force[m].x)) * cool;
          pos[m].y += Math.max(-8, Math.min(8, force[m].y)) * cool;
          pos[m].x = Math.max(60, Math.min(VIEW_W - 60, pos[m].x));
          pos[m].y = Math.max(55, Math.min(VIEW_H - 60, pos[m].y));
        });
      }
      this._positions = pos;
      this._applyLayoutOverrides();
    }

    // ── Rendering ──────────────────────────────────────────

    _render() {
      const style = `
        :host { display: block; }
        ha-card { overflow: hidden; }
        .header {
          padding: 12px 16px 0;
          font-size: 1.25em;
          font-weight: 500;
          color: var(--primary-text-color);
        }
        .graph { width: 100%; display: block; position: relative; }
        .graph.editing svg { touch-action: none; }
        svg { width: 100%; height: auto; display: block; }
        .floorplan { opacity: 0.9; }
        .toolbar {
          display: flex; flex-wrap: wrap; gap: 6px;
          padding: 8px 16px 4px; justify-content: flex-end;
        }
        .tool-btn {
          border: 1px solid var(--divider-color, #e0e0e0);
          background: transparent; color: var(--primary-text-color);
          border-radius: 14px; padding: 4px 12px; font-size: 0.85em; cursor: pointer;
        }
        .tool-btn:hover { background: var(--secondary-background-color, #f0f0f0); }
        .tool-btn.primary {
          background: var(--primary-color, #03a9f4);
          border-color: var(--primary-color, #03a9f4);
          color: var(--text-primary-color, #fff);
        }
        .tool-size {
          display: inline-flex; align-items: center; gap: 6px;
          padding: 0 6px; color: var(--secondary-text-color);
        }
        .tool-size .size-input { width: 90px; cursor: pointer; accent-color: var(--primary-color, #03a9f4); }
        .tool-size .size-glyph { color: var(--secondary-text-color); line-height: 1; }
        .tool-size .size-glyph.small { font-size: 0.7em; }
        .tool-size .size-glyph.large { font-size: 1.05em; }
        .tool-style {
          display: inline-flex; border: 1px solid var(--divider-color, #e0e0e0);
          border-radius: 14px; overflow: hidden;
        }
        .tool-style .style-opt {
          border: none; background: transparent; cursor: pointer;
          color: var(--secondary-text-color); font-size: 0.82em; padding: 4px 10px;
        }
        .tool-style .style-opt.active {
          background: var(--primary-color, #03a9f4);
          color: var(--text-primary-color, #fff);
        }
        .edit-hint {
          padding: 0 16px 8px; font-size: 0.8em;
          color: var(--secondary-text-color);
        }
        .toast {
          position: absolute; left: 50%; bottom: 10px; transform: translateX(-50%);
          background: color-mix(in srgb, var(--card-background-color, #333) 82%, transparent);
          color: var(--primary-text-color);
          border: 1px solid var(--divider-color, #e0e0e0);
          padding: 4px 12px; border-radius: 14px; font-size: 0.8em;
          pointer-events: none; white-space: nowrap;
        }
        .spark-outage { font-size: 0.78em; color: var(--error-color, #e53935); padding: 2px 0 0; }
        .mesh-bg { fill: url(#mesh-bg); }
        .edge { cursor: pointer; filter: drop-shadow(0 1px 2px rgba(0, 0, 0, 0.18)); }
        .edge-hit { stroke: transparent; stroke-width: 14; cursor: pointer; }
        .edge-label {
          font: 11px sans-serif;
          fill: var(--secondary-text-color, #666);
          text-anchor: middle;
          pointer-events: none;
          paint-order: stroke;
          stroke: var(--card-background-color, #fff);
          stroke-width: 3.5px;
          stroke-linejoin: round;
        }
        .node { cursor: pointer; }
        .adapter-body { filter: drop-shadow(0 8px 11px rgba(0, 0, 0, 0.32)); }
        .adapter-photo { pointer-events: none; }
        .adapter-aura { fill: currentColor; opacity: 0.16; filter: blur(7px); }
        .adapter-quality { fill: none; stroke-width: 2.5; stroke: currentColor; opacity: 0.9; }
        .node-label {
          font: 10px sans-serif;
          fill: var(--primary-text-color, #212121);
          pointer-events: none;
          paint-order: stroke;
          stroke: var(--card-background-color, #fff);
          stroke-width: 3px;
          stroke-linejoin: round;
        }
        .node-sub {
          font: 9px sans-serif;
          fill: var(--secondary-text-color, #666);
          pointer-events: none;
          paint-order: stroke;
          stroke: var(--card-background-color, #fff);
          stroke-width: 3px;
          stroke-linejoin: round;
        }
        .selected-ring { fill: none; stroke: var(--primary-color, #03a9f4); stroke-width: 2.5; }
        .cco-ring { fill: none; stroke: var(--primary-color, #03a9f4); stroke-width: 1.5; stroke-dasharray: 3 2; }
        .details {
          border-top: 1px solid var(--divider-color, #e0e0e0);
          padding: 8px 16px 12px;
          font-size: 1.02em;
          color: var(--primary-text-color);
        }
        .details table { border-collapse: collapse; width: 100%; }
        .details td { padding: 3px 8px 3px 0; vertical-align: top; }
        .info-title {
          font-size: 0.9em; font-weight: 500; color: var(--secondary-text-color);
          margin: 10px 0 4px; padding-top: 8px;
          border-top: 1px solid var(--divider-color, #e0e0e0);
        }
        .details td:first-child { color: var(--secondary-text-color); white-space: nowrap; }
        .hint {
          padding: 4px 16px 12px;
          font-size: 0.8em;
          color: var(--secondary-text-color);
        }
        .error { padding: 16px; color: var(--error-color, #e53935); }
        .analysis {
          display: flex; flex-wrap: wrap; gap: 4px 16px;
          padding: 0 16px 8px; font-size: 0.8em;
          color: var(--secondary-text-color);
        }
        .ranges { display: flex; gap: 6px; margin: 8px 0 4px; }
        .ranges button {
          border: 1px solid var(--divider-color, #e0e0e0);
          background: transparent;
          color: var(--primary-text-color);
          border-radius: 12px;
          padding: 2px 10px;
          font-size: 0.85em;
          cursor: pointer;
        }
        .ranges button.active {
          background: var(--primary-color, #03a9f4);
          border-color: var(--primary-color, #03a9f4);
          color: var(--text-primary-color, #fff);
        }
        .spark-wrap { position: relative; width: 100%; margin-top: 2px; }
        .spark { width: 100%; height: 70px; display: block; touch-action: none; }
        /* HTML overlay so the readout is not distorted by the SVG's
           non-uniform (preserveAspectRatio=none) horizontal scaling. */
        .spark-readout {
          position: absolute; top: 2px; left: 4px;
          font-size: 0.98em; font-weight: 500;
          color: var(--primary-text-color);
          background: color-mix(in srgb, var(--card-background-color, #fff) 78%, transparent);
          padding: 0 4px; border-radius: 4px;
          pointer-events: none; white-space: nowrap;
        }
        .spark-cursor {
          position: absolute; top: 0; bottom: 0; width: 1px;
          background: var(--secondary-text-color, #666);
          opacity: 0.6; display: none; pointer-events: none;
        }
        .spark-dot {
          position: absolute; width: 8px; height: 8px; border-radius: 50%;
          background: var(--primary-color, #03a9f4);
          border: 1.5px solid var(--card-background-color, #fff);
          transform: translate(-50%, -50%); display: none; pointer-events: none;
        }
        .spark-empty { font-size: 0.8em; color: var(--secondary-text-color); padding: 4px 0; }
        .empty { padding: 16px; color: var(--secondary-text-color); }
        .legend {
          display: flex; flex-wrap: wrap; gap: 12px;
          padding: 0 16px 12px; font-size: 0.8em;
          color: var(--secondary-text-color);
        }
        .legend span::before {
          content: ""; display: inline-block; width: 10px; height: 10px;
          border-radius: 2px; margin-right: 4px;
          background: var(--dot, #999);
        }
        .quality-dot {
          display: inline-block; width: 9px; height: 9px; border-radius: 50%;
          margin-right: 5px; vertical-align: baseline;
          background: var(--dot, #999);
        }
        .controls {
          margin-bottom: 4px;
        }
        .controls-title {
          font-size: 0.9em; font-weight: 500; color: var(--secondary-text-color);
          margin-bottom: 6px;
        }
        .control-row {
          display: flex; align-items: center; justify-content: space-between;
          gap: 12px; padding: 4px 0;
        }
        .control-row .label { color: var(--primary-text-color); }
        .control-row.unavailable .label { color: var(--secondary-text-color); }
        /* iOS-style toggle */
        .toggle {
          position: relative; width: 40px; height: 22px; flex: none;
          border-radius: 11px; border: none; padding: 0; cursor: pointer;
          background: var(--switch-unchecked-track-color, #9e9e9e);
          transition: background 0.15s ease;
        }
        .toggle[aria-checked="true"] {
          background: var(--primary-color, #03a9f4);
        }
        .toggle::after {
          content: ""; position: absolute; top: 2px; left: 2px;
          width: 18px; height: 18px; border-radius: 50%; background: #fff;
          transition: transform 0.15s ease;
        }
        .toggle[aria-checked="true"]::after { transform: translateX(18px); }
        .toggle:disabled { opacity: 0.5; cursor: default; }
        .control-select {
          background: var(--card-background-color, #fff);
          color: var(--primary-text-color);
          border: 1px solid var(--divider-color, #e0e0e0);
          border-radius: 8px; padding: 4px 8px; font-size: 0.9em; cursor: pointer;
        }
        .control-select:disabled { opacity: 0.5; cursor: default; }
        .control-btn {
          border: 1px solid var(--divider-color, #e0e0e0);
          background: transparent; color: var(--primary-text-color);
          border-radius: 8px; padding: 4px 12px; font-size: 0.9em; cursor: pointer;
        }
        .control-btn:hover { background: var(--secondary-background-color, #f0f0f0); }
        .control-btn:disabled { opacity: 0.5; cursor: default; }
      `;

      let body;
      if (this._error) {
        body = `<div class="error">Powerline topology: ${this._escape(
          this._error
        )}</div>`;
      } else if (!this._topology || !this._topology.nodes.length) {
        body = `<div class="empty">${this._escape(this._t("empty"))}</div>`;
      } else {
        const editHint = this._editing
          ? `<div class="edit-hint">${this._escape(this._t("edit_hint"))}</div>`
          : "";
        const toast = this._toast
          ? `<div class="toast">${this._escape(this._toast)}</div>`
          : "";
        body =
          `${this._renderToolbar()}` +
          `<div class="graph${this._editing ? " editing" : ""}">${this._renderSvg()}${toast}</div>` +
          `${editHint}${this._renderLegend()}${this._renderAnalysis()}${this._renderDetails()}`;
      }

      const header = this._config.title
        ? `<div class="header">${this._escape(this._config.title)}</div>`
        : "";
      this.shadowRoot.innerHTML = `
        <style>${style}</style>
        <ha-card>
          ${header}
          ${body}
        </ha-card>
      `;
      this._bindEvents();
    }

    _renderSvg() {
      const t = this._topology;
      const parts = [];
      // Graph centroid: labels are pushed away from it so they end up on
      // the outside of the mesh instead of on top of each other.
      const placed = t.nodes
        .map((n) => this._positions[n.mac])
        .filter(Boolean);
      const cx = placed.reduce((s, p) => s + p.x, 0) / Math.max(1, placed.length);
      const cy = placed.reduce((s, p) => s + p.y, 0) / Math.max(1, placed.length);
      parts.push(
        `<svg viewBox="0 0 ${VIEW_W} ${VIEW_H}" preserveAspectRatio="xMidYMid meet">` +
          `<defs><linearGradient id="mesh-bg" x1="0" y1="0" x2="1" y2="1">` +
          `<stop offset="0" stop-color="var(--primary-color, #03a9f4)" stop-opacity="0.12"></stop>` +
          `<stop offset="1" stop-color="var(--card-background-color, #fff)" stop-opacity="0"></stop>` +
          `</linearGradient>` +
          `<clipPath id="mesh-clip"><rect x="0" y="0" width="${VIEW_W}" height="${VIEW_H}" rx="18"></rect></clipPath></defs>` +
          `<rect class="mesh-bg" x="0" y="0" width="${VIEW_W}" height="${VIEW_H}" rx="18"></rect>`
      );

      // Optional floor-plan background behind the mesh. Clipped to the card's
      // rounded rectangle; "meet" keeps the whole plan visible (letterboxed).
      if (this._userLayout && this._userLayout.background) {
        parts.push(
          `<image class="floorplan" href="${this._userLayout.background}" x="0" y="0"` +
            ` width="${VIEW_W}" height="${VIEW_H}" preserveAspectRatio="xMidYMid meet"` +
            ` clip-path="url(#mesh-clip)"></image>`
        );
      }

      // Edges below nodes
      t.edges.forEach((e, i) => {
        const a = this._edgeAnchor(e.source);
        const b = this._edgeAnchor(e.destination);
        if (!a || !b) return;
        const color = QUALITY_COLORS[e.link_quality] || QUALITY_COLORS.unknown;
        const width = Math.min(8, 1.5 + e.average_rate / 200);
        const dash = e.estimated ? ` stroke-dasharray="7 5"` : "";
        const sel =
          this._selected &&
          this._selected.kind === "edge" &&
          this._selected.id === i;
        parts.push(
          `<line class="edge" data-edge="${i}" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"` +
            ` stroke="${color}" stroke-width="${sel ? width + 2 : width}"` +
            ` stroke-linecap="round"${dash} opacity="${sel ? 1 : 0.85}"></line>` +
            `<line class="edge-hit" data-edge="${i}" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"></line>`
        );
        if (e.average_rate > 0) {
          // Place the label beside the line (perpendicular offset), on the
          // side facing away from the graph centre so it stays clear of the
          // mesh interior and the node labels.
          const mx = (a.x + b.x) / 2;
          const my = (a.y + b.y) / 2;
          const dx = b.x - a.x;
          const dy = b.y - a.y;
          const len = Math.max(1, Math.sqrt(dx * dx + dy * dy));
          let nx = -dy / len;
          let ny = dx / len;
          if (nx * (mx - cx) + ny * (my - cy) < 0) {
            nx = -nx;
            ny = -ny;
          }
          const lx = mx + nx * 14;
          const ly = my + ny * 14 + 4;
          parts.push(
            `<text class="edge-label" x="${lx.toFixed(1)}" y="${ly.toFixed(
              1
            )}">${e.average_rate} Mbit/s${e.estimated ? " ~" : ""}</text>`
          );
        }
      });

      // Nodes
      t.nodes.forEach((node) => {
        const p = this._positions[node.mac];
        if (!p) return;
        const nodeQuality = this._nodeQuality(node);
        const fill = node.online
          ? QUALITY_COLORS[nodeQuality] || QUALITY_COLORS.unknown
          : QUALITY_COLORS.red;
        const led = this._adapterLedState(node.mac);
        const sel =
          this._selected &&
          this._selected.kind === "node" &&
          this._selected.id === node.mac;
        const ringS = this._iconScale();
        parts.push(`<g class="node" data-node="${this._escape(node.mac)}">`);
        if (node.role === "CCo") {
          parts.push(`<circle class="cco-ring" cx="${p.x}" cy="${p.y}" r="${(26 * ringS).toFixed(1)}"></circle>`);
        }
        if (sel) {
          parts.push(`<circle class="selected-ring" cx="${p.x}" cy="${p.y}" r="${(30 * ringS).toFixed(1)}"></circle>`);
        }
        parts.push(this._adapterIconSvg(p, fill, led, node.online));
        const label = node.name === node.mac ? this._shortMac(node.mac) : node.name;
        const sub = node.role === "CCo" ? this._t("badge_CCo") : "";
        parts.push(this._nodeLabelSvg(p, cx, cy, label, sub));
        parts.push(`</g>`);
      });

      parts.push(`</svg>`);
      return parts.join("");
    }

    // Toolbar above the graph: an "Arrange" toggle, and while arranging,
    // buttons to upload/remove the floor-plan background and reset positions.
    _renderToolbar() {
      if (!this._editing) {
        return (
          `<div class="toolbar">` +
          `<button class="tool-btn" data-edit-toggle title="${this._escape(
            this._t("edit")
          )}">✥ ${this._escape(this._t("edit"))}</button>` +
          `</div>`
        );
      }
      const removeBg = this._userLayout.background
        ? `<button class="tool-btn" data-bg-remove>${this._escape(this._t("bg_remove"))}</button>`
        : "";
      const scale = this._iconScale();
      const sizeSlider =
        `<label class="tool-size" title="${this._escape(this._t("icon_size"))}">` +
        `<span class="size-glyph small">▪</span>` +
        `<input type="range" class="size-input" min="0.5" max="2.5" step="0.1" value="${scale}">` +
        `<span class="size-glyph large">◼</span>` +
        `</label>`;
      const style = this._iconStyle();
      const styleToggle =
        `<div class="tool-style" role="group" title="${this._escape(this._t("icon_style"))}">` +
        `<button class="style-opt${style === "chatgpt" ? " active" : ""}" data-icon-style="chatgpt">ChatGPT</button>` +
        `<button class="style-opt${style === "claude" ? " active" : ""}" data-icon-style="claude">Claude</button>` +
        `</div>`;
      return (
        `<div class="toolbar editing">` +
        styleToggle +
        sizeSlider +
        `<button class="tool-btn" data-bg-upload>🖼 ${this._escape(this._t("bg_upload"))}</button>` +
        removeBg +
        `<button class="tool-btn" data-reset-positions>${this._escape(this._t("reset_positions"))}</button>` +
        `<button class="tool-btn primary" data-edit-toggle>✓ ${this._escape(this._t("edit_done"))}</button>` +
        `<input type="file" accept="image/*" class="bg-input" hidden>` +
        `</div>`
      );
    }

    _renderLegend() {
      const items = [
        ["#43a047", "&gt; 700 Mbit/s"],
        ["#fdd835", "400–700"],
        ["#fb8c00", "150–400"],
        ["#e53935", "&lt; 150"],
      ]
        .map(([c, l]) => `<span style="--dot:${c}">${l}</span>`)
        .join("");
      const estimated = (this._topology.edges || []).some((e) => e.estimated)
        ? `<span style="--dot:transparent">${this._escape(this._t("legend_estimated"))}</span>`
        : "";
      return `<div class="legend">${items}${estimated}</div>`;
    }

    _renderAnalysis() {
      const analysis = (this._topology && this._topology.analysis) || {};
      const parts = [];
      if (analysis.worst_link && analysis.worst_link.average_rate > 0) {
        const w = analysis.worst_link;
        parts.push(
          `<span>🐢 ${this._t("analysis_worst")}: ${this._nodeName(w.source)} ↔ ${this._nodeName(
            w.destination
          )} (${w.average_rate} Mbit/s)</span>`
        );
      }
      if (analysis.most_unstable_link) {
        const u = analysis.most_unstable_link;
        parts.push(
          `<span>📉 ${this._t("analysis_unstable")}: ${this._nodeName(u.source)} ↔ ${this._nodeName(
            u.destination
          )} (±${Math.round(u.instability * 100)}%)</span>`
        );
      }
      if (analysis.offline_adapters && analysis.offline_adapters.length) {
        parts.push(`<span>🔴 ${this._t("analysis_offline")}: ${analysis.offline_adapters.length}</span>`);
      }
      return parts.length ? `<div class="analysis">${parts.join("")}</div>` : "";
    }

    _renderHistory(edge) {
      const key = `${edge.source}|${edge.destination}`;
      const ranges = [
        [1, "1 h"],
        [24, "24 h"],
        [168, this._t("range_168")],
        [720, this._t("range_720")],
      ];
      const buttons = ranges
        .map(
          ([h, label]) =>
            `<button data-hours="${h}" class="${
              this._historyHours === h ? "active" : ""
            }">${label}</button>`
        )
        .join("");

      let chart;
      if (this._historyLoading) {
        chart = `<div class="spark-empty">${this._escape(this._t("history_loading"))}</div>`;
      } else if (!this._history || this._history.key !== key) {
        chart = `<div class="spark-empty"></div>`;
      } else if (this._history.series.length < 2) {
        chart = `<div class="spark-empty">${this._escape(this._t("history_empty"))}</div>`;
      } else {
        chart = this._renderSparkline(this._history.series);
      }
      return `<div class="ranges">${buttons}</div>${chart}`;
    }

    // Split a time series into contiguous segments, breaking wherever the
    // gap between two consecutive samples is much larger than the typical
    // spacing. A gap means the adapter was offline/disconnected during that
    // time, so no link speed existed — we must NOT draw a line across it
    // (that would make an outage look like continuous availability).
    _segmentSeries(series) {
      if (series.length < 2) return { segments: [series.slice()], gaps: [] };
      const deltas = [];
      for (let i = 1; i < series.length; i++) {
        deltas.push(series[i].t - series[i - 1].t);
      }
      const sorted = deltas.slice().sort((a, b) => a - b);
      const median = sorted[Math.floor(sorted.length / 2)] || 1;
      // 2.5× the median spacing (with a small floor) counts as an outage.
      const threshold = Math.max(median * 2.5, median + 60);
      const segments = [];
      const gaps = [];
      let current = [series[0]];
      for (let i = 1; i < series.length; i++) {
        if (series[i].t - series[i - 1].t > threshold) {
          segments.push(current);
          gaps.push([series[i - 1], series[i]]);
          current = [];
        }
        current.push(series[i]);
      }
      segments.push(current);
      return { segments, gaps };
    }

    _renderSparkline(series) {
      const W = 300;
      const H = 70;
      const PAD = 4;
      const ts = series.map((p) => p.t);
      const t0 = Math.min(...ts);
      const t1 = Math.max(...ts);
      const lo = Math.min(...series.map((p) => (p.min != null ? p.min : p.avg)));
      const hi = Math.max(...series.map((p) => (p.max != null ? p.max : p.avg)));
      const x = (t) =>
        PAD + ((t - t0) / Math.max(1, t1 - t0)) * (W - 2 * PAD);
      const y = (v) =>
        H - PAD - ((v - lo) / Math.max(1, hi - lo)) * (H - 2 * PAD - 10);

      // Stash the scale so the hover handler can map a mouse position back to
      // the nearest sample and place the crosshair/readout.
      this._sparkMeta = { series, t0, t1, lo, hi, W, H, PAD, hours: this._historyHours };

      const { segments, gaps } = this._segmentSeries(series);
      const hasBand = series.some((p) => p.min != null);

      const svgParts = [];
      // One band + one polyline per contiguous segment, so gaps stay empty.
      segments.forEach((seg) => {
        if (!seg.length) return;
        if (hasBand && seg.length > 1) {
          const upper = seg.map(
            (p) => `${x(p.t).toFixed(1)},${y(p.max != null ? p.max : p.avg).toFixed(1)}`
          );
          const lower = seg
            .slice()
            .reverse()
            .map((p) => `${x(p.t).toFixed(1)},${y(p.min != null ? p.min : p.avg).toFixed(1)}`);
          svgParts.push(
            `<polygon points="${upper.join(" ")} ${lower.join(" ")}" fill="var(--primary-color, #03a9f4)" opacity="0.15"></polygon>`
          );
        }
        if (seg.length > 1) {
          const line = seg.map((p) => `${x(p.t).toFixed(1)},${y(p.avg).toFixed(1)}`).join(" ");
          svgParts.push(
            `<polyline points="${line}" fill="none" stroke="var(--primary-color, #03a9f4)" stroke-width="1.5"></polyline>`
          );
        } else {
          const p = seg[0];
          svgParts.push(
            `<circle cx="${x(p.t).toFixed(1)}" cy="${y(p.avg).toFixed(1)}" r="1.8" fill="var(--primary-color, #03a9f4)"></circle>`
          );
        }
      });
      // Outage markers: a red band along the bottom spanning each gap makes
      // "adapter was offline here" unmistakable at a glance.
      gaps.forEach(([a, b]) => {
        const x1 = x(a.t);
        const x2 = x(b.t);
        svgParts.push(
          `<rect class="spark-gap" x="${x1.toFixed(1)}" y="${(H - 3).toFixed(1)}" width="${Math.max(
            1,
            x2 - x1
          ).toFixed(1)}" height="3" fill="${QUALITY_COLORS.red}" opacity="0.9"></rect>`
        );
        svgParts.push(
          `<line x1="${x1.toFixed(1)}" y1="0" x2="${x1.toFixed(1)}" y2="${H}" stroke="${QUALITY_COLORS.red}" stroke-width="1" stroke-dasharray="2 2" vector-effect="non-scaling-stroke" opacity="0.5"></line>` +
            `<line x1="${x2.toFixed(1)}" y1="0" x2="${x2.toFixed(1)}" y2="${H}" stroke="${QUALITY_COLORS.red}" stroke-width="1" stroke-dasharray="2 2" vector-effect="non-scaling-stroke" opacity="0.5"></line>`
        );
      });

      const last = series[series.length - 1];
      // Default readout (no hover) = maximum over the visible range. The
      // crosshair line uses a non-scaling stroke so it stays 1px despite the
      // SVG's horizontal stretch; the dot is an HTML overlay for the same
      // reason. Both are hidden until the pointer enters the chart.
      const svg =
        `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">` +
        svgParts.join("") +
        `<circle cx="${x(last.t).toFixed(1)}" cy="${y(last.avg).toFixed(1)}" r="2.5" fill="var(--primary-color, #03a9f4)"></circle>` +
        `<line class="spark-cursor-line" x1="0" y1="0" x2="0" y2="${H}"` +
        ` stroke="var(--secondary-text-color, #666)" stroke-width="1"` +
        ` vector-effect="non-scaling-stroke" opacity="0" pointer-events="none"></line>` +
        `</svg>`;
      const defaultLabel = `Max ${hi} Mbit/s`;
      const outageNote = gaps.length
        ? `<div class="spark-outage">⚠ ${this._escape(this._t("outage"))}</div>`
        : "";
      return (
        `<div class="spark-wrap">` +
        `<div class="spark-readout" data-default="${this._escape(defaultLabel)}">${this._escape(defaultLabel)}</div>` +
        svg +
        `<div class="spark-dot"></div>` +
        `</div>` +
        outageNote
      );
    }

    _renderDetails() {
      if (!this._selected) {
        return `<div class="hint">${this._escape(this._t("hint_click"))}</div>`;
      }
      // Each row is [key, value, isHtml?]. When isHtml is true the value is
      // treated as trusted markup (used for the coloured quality label).
      const rows = [];
      let controls = "";
      if (this._selected.kind === "node") {
        const node = this._topology.nodes.find(
          (n) => n.mac === this._selected.id
        );
        if (!node) return "";
        rows.push([this._t("name"), node.name]);
        rows.push([this._t("mac"), node.mac]);
        if (node.model) rows.push([this._t("model"), node.model]);
        if (node.firmware) rows.push([this._t("firmware"), node.firmware]);
        if (node.manufacturer) rows.push([this._t("manufacturer"), node.manufacturer]);
        if (node.chipset) rows.push([this._t("chipset"), node.chipset]);
        rows.push([this._t("role"), this._roleLabel(node.role)]);
        rows.push([this._t("status"), node.online ? this._t("online") : this._t("offline")]);
        rows.push([this._t("last_update"), this._formatTime(node.last_update)]);
        controls = this._renderAdapterControls(node.mac);
      } else {
        const edge = this._topology.edges[this._selected.id];
        if (!edge) return "";
        rows.push([
          this._t("connection"),
          `${this._nodeName(edge.source)} ↔ ${this._nodeName(edge.destination)}`,
        ]);
        rows.push(["TX", `${edge.tx_phy_rate} Mbit/s`]);
        rows.push(["RX", `${edge.rx_phy_rate} Mbit/s`]);
        rows.push([this._t("average"), `${edge.average_rate} Mbit/s`]);
        rows.push([this._t("quality"), this._qualityLabelHtml(edge.link_quality), true]);
        if (edge.estimated) rows.push([this._t("note"), this._t("note_estimated")]);
        rows.push([this._t("last_update"), this._formatTime(edge.timestamp)]);
      }
      const table = rows
        .map(
          ([k, v, isHtml]) =>
            `<tr><td>${this._escape(k)}</td><td>${
              isHtml ? v : this._escape(String(v))
            }</td></tr>`
        )
        .join("");
      let history = "";
      if (this._selected.kind === "edge") {
        const edge = this._topology.edges[this._selected.id];
        if (edge) history = this._renderHistory(edge);
      }
      // For an adapter the controls sit above the info block (as requested);
      // the "Info:" heading only appears when controls precede the table.
      const infoTitle = controls ? `<div class="info-title">${this._escape(this._t("info"))}</div>` : "";
      return `<div class="details">${controls}${infoTitle}<table>${table}</table>${history}</div>`;
    }

    _bindEvents() {
      this._bindToolbar();
      // While arranging, adapters are draggable instead of clickable and the
      // edge/detail interactions are suspended to keep the surface calm.
      if (this._editing) {
        this._bindDrag();
        return;
      }
      this.shadowRoot.querySelectorAll("[data-node]").forEach((el) => {
        el.addEventListener("click", () => {
          const mac = el.getAttribute("data-node");
          this._selected =
            this._selected &&
            this._selected.kind === "node" &&
            this._selected.id === mac
              ? null
              : { kind: "node", id: mac };
          this._render();
        });
      });
      this.shadowRoot.querySelectorAll("[data-edge]").forEach((el) => {
        el.addEventListener("click", () => {
          const idx = Number(el.getAttribute("data-edge"));
          const deselect =
            this._selected &&
            this._selected.kind === "edge" &&
            this._selected.id === idx;
          this._selected = deselect ? null : { kind: "edge", id: idx };
          this._history = null;
          this._render();
          if (!deselect) {
            const edge = this._topology.edges[idx];
            if (edge) this._fetchHistory(edge, this._historyHours);
          }
        });
      });
      this.shadowRoot.querySelectorAll(".ranges button").forEach((el) => {
        el.addEventListener("click", () => {
          if (!this._selected || this._selected.kind !== "edge") return;
          const edge = this._topology.edges[this._selected.id];
          if (edge) this._fetchHistory(edge, Number(el.getAttribute("data-hours")));
        });
      });
      this._bindSparkHover();
      this._bindControls();
    }

    _bindToolbar() {
      const toggle = this.shadowRoot.querySelector("[data-edit-toggle]");
      if (toggle) {
        toggle.addEventListener("click", () => {
          this._editing = !this._editing;
          this._selected = null;
          this._render();
        });
      }
      const upload = this.shadowRoot.querySelector("[data-bg-upload]");
      const input = this.shadowRoot.querySelector(".bg-input");
      if (upload && input) {
        upload.addEventListener("click", () => input.click());
        input.addEventListener("change", () => {
          const file = input.files && input.files[0];
          if (file) this._loadBackgroundFile(file);
        });
      }
      const removeBg = this.shadowRoot.querySelector("[data-bg-remove]");
      if (removeBg) {
        removeBg.addEventListener("click", () => {
          this._userLayout.background = null;
          this._saveLayout({ background: null });
          this._render();
        });
      }
      const reset = this.shadowRoot.querySelector("[data-reset-positions]");
      if (reset) {
        reset.addEventListener("click", () => {
          this._userLayout.positions = {};
          this._saveLayout({ positions: {} });
          this._layout(); // recompute automatic layout
          this._render();
        });
      }
      const size = this.shadowRoot.querySelector(".size-input");
      if (size) {
        // Live resize while sliding (redraw only the SVG so the slider keeps
        // focus); persist once the user lets go.
        size.addEventListener("input", () => {
          this._userLayout.iconScale = Number(size.value);
          this._refreshGraphSvg();
        });
        const commit = () => this._saveLayout({ icon_scale: this._iconScale() });
        size.addEventListener("change", commit);
      }
      this.shadowRoot.querySelectorAll("[data-icon-style]").forEach((el) => {
        el.addEventListener("click", () => {
          const style = el.getAttribute("data-icon-style");
          if (style === this._iconStyle()) return;
          this._userLayout.iconStyle = style;
          this._saveLayout({ icon_style: style });
          this._render();
        });
      });
    }

    // Replace just the graph's SVG in place (used by the size slider) so the
    // toolbar and its native range input are not torn down mid-interaction.
    _refreshGraphSvg() {
      const graph = this.shadowRoot.querySelector(".graph");
      const svg = graph && graph.querySelector("svg");
      if (!graph || !svg) return;
      const tmp = document.createElement("div");
      tmp.innerHTML = this._renderSvg();
      const fresh = tmp.querySelector("svg");
      if (fresh) {
        svg.replaceWith(fresh);
        if (this._editing) this._bindDrag();
      }
    }

    // Read an uploaded floor plan, downscale it so the stored data URL stays
    // small, and persist it as the background.
    _loadBackgroundFile(file) {
      const reader = new FileReader();
      reader.onload = () => {
        const img = new Image();
        img.onload = () => {
          const MAX = 1600;
          const scale = Math.min(1, MAX / Math.max(img.width, img.height));
          const w = Math.round(img.width * scale);
          const h = Math.round(img.height * scale);
          const canvas = document.createElement("canvas");
          canvas.width = w;
          canvas.height = h;
          canvas.getContext("2d").drawImage(img, 0, 0, w, h);
          // JPEG keeps photographic floor plans compact; PNG would balloon.
          let dataUrl = canvas.toDataURL("image/jpeg", 0.82);
          if (dataUrl.length > 4 * 1024 * 1024) {
            dataUrl = canvas.toDataURL("image/jpeg", 0.6);
          }
          if (dataUrl.length > 4 * 1024 * 1024) {
            this._showToast(this._t("bg_too_large"));
            return;
          }
          this._userLayout.background = dataUrl;
          this._saveLayout({ background: dataUrl });
          this._render();
        };
        img.onerror = () => this._showToast(this._t("bg_too_large"));
        img.src = reader.result;
      };
      reader.readAsDataURL(file);
    }

    // Drag adapters to their real-world position. Pointer coordinates are
    // mapped into SVG user space via the inverse screen CTM, which correctly
    // accounts for the viewBox scaling and letterboxing.
    _bindDrag() {
      const svg = this.shadowRoot.querySelector(".graph svg");
      if (!svg) return;
      const toSvg = (ev) => {
        const pt = svg.createSVGPoint();
        pt.x = ev.clientX;
        pt.y = ev.clientY;
        const ctm = svg.getScreenCTM();
        if (!ctm) return null;
        const p = pt.matrixTransform(ctm.inverse());
        return {
          x: Math.max(20, Math.min(VIEW_W - 20, p.x)),
          y: Math.max(20, Math.min(VIEW_H - 20, p.y)),
        };
      };
      this.shadowRoot.querySelectorAll("[data-node]").forEach((el) => {
        el.style.cursor = "grab";
        el.addEventListener("pointerdown", (ev) => {
          ev.preventDefault();
          const mac = el.getAttribute("data-node");
          const base = this._positions[mac] || { x: 0, y: 0 };
          this._drag = { mac, moved: false, base: { x: base.x, y: base.y } };
          el.setPointerCapture(ev.pointerId);
          el.style.cursor = "grabbing";
        });
        el.addEventListener("pointermove", (ev) => {
          if (!this._drag || this._drag.mac !== el.getAttribute("data-node")) return;
          const p = toSvg(ev);
          if (!p) return;
          this._drag.moved = true;
          this._positions[this._drag.mac] = p;
          // Translate the whole node group (icon + rings + label) so the drag
          // stays cheap and works for any icon style; a full render on drop
          // reflows the connected edges and labels.
          const base = this._drag.base;
          el.setAttribute(
            "transform",
            `translate(${(p.x - base.x).toFixed(1)} ${(p.y - base.y).toFixed(1)})`
          );
        });
        const end = (ev) => {
          if (!this._drag) return;
          const mac = this._drag.mac;
          const moved = this._drag.moved;
          this._drag = null;
          el.style.cursor = "grab";
          try {
            el.releasePointerCapture(ev.pointerId);
          } catch (e) {
            /* pointer already released */
          }
          if (moved) {
            const p = this._positions[mac];
            this._userLayout.positions[mac] = { x: p.x, y: p.y };
            this._saveLayout({ positions: this._userLayout.positions });
            this._render(); // redraw edges/labels at final position
          }
        };
        el.addEventListener("pointerup", end);
        el.addEventListener("pointercancel", end);
      });
    }

    // Live readout: while the pointer is over the sparkline show the value at
    // the nearest sample; on leave fall back to the range maximum.
    _bindSparkHover() {
      const wrap = this.shadowRoot.querySelector(".spark-wrap");
      if (!wrap || !this._sparkMeta) return;
      const readout = wrap.querySelector(".spark-readout");
      const cursor = wrap.querySelector(".spark-cursor-line");
      const dot = wrap.querySelector(".spark-dot");
      const meta = this._sparkMeta;
      const span = Math.max(1, meta.t1 - meta.t0);
      const x = (t) =>
        meta.PAD + ((t - meta.t0) / span) * (meta.W - 2 * meta.PAD);
      const y = (v) =>
        meta.H - meta.PAD -
        ((v - meta.lo) / Math.max(1, meta.hi - meta.lo)) *
          (meta.H - 2 * meta.PAD - 10);

      const move = (ev) => {
        const rect = wrap.getBoundingClientRect();
        if (rect.width <= 0) return;
        const frac = Math.min(1, Math.max(0, (ev.clientX - rect.left) / rect.width));
        const tTarget = meta.t0 + frac * span;
        let best = meta.series[0];
        for (const p of meta.series) {
          if (Math.abs(p.t - tTarget) < Math.abs(best.t - tTarget)) best = p;
        }
        const leftPct = (x(best.t) / meta.W) * 100;
        const topPct = (y(best.avg) / meta.H) * 100;
        if (cursor) {
          cursor.setAttribute("x1", x(best.t).toFixed(1));
          cursor.setAttribute("x2", x(best.t).toFixed(1));
          cursor.setAttribute("opacity", "0.6");
        }
        if (dot) {
          dot.style.left = `${leftPct}%`;
          dot.style.top = `${topPct}%`;
          dot.style.display = "block";
        }
        if (readout) {
          readout.textContent = `${best.avg} Mbit/s · ${this._formatSampleTime(
            best.t,
            meta.hours
          )}`;
        }
      };
      const leave = () => {
        if (cursor) cursor.setAttribute("opacity", "0");
        if (dot) dot.style.display = "none";
        if (readout) readout.textContent = readout.getAttribute("data-default") || "";
      };
      wrap.addEventListener("pointermove", move);
      wrap.addEventListener("pointerleave", leave);
    }

    _bindControls() {
      this.shadowRoot.querySelectorAll("[data-toggle]").forEach((el) => {
        el.addEventListener("click", () => {
          if (el.disabled) return;
          const entityId = el.getAttribute("data-toggle");
          const turnOn = el.getAttribute("aria-checked") !== "true";
          // Optimistic UI; the next topology/state update reconciles it.
          el.setAttribute("aria-checked", String(turnOn));
          this._hass.callService("switch", turnOn ? "turn_on" : "turn_off", {
            entity_id: entityId,
          });
        });
      });
      this.shadowRoot.querySelectorAll("[data-select]").forEach((el) => {
        el.addEventListener("change", () => {
          const entityId = el.getAttribute("data-select");
          this._hass.callService("select", "select_option", {
            entity_id: entityId,
            option: el.value,
          });
        });
      });
      this.shadowRoot.querySelectorAll("[data-press]").forEach((el) => {
        el.addEventListener("click", () => {
          if (el.disabled) return;
          const entityId = el.getAttribute("data-press");
          this._hass.callService("button", "press", { entity_id: entityId });
        });
      });
    }

    // Node label placed on the side of the node facing away from the graph
    // centre (so it doesn't sit on the edges), clamped to the viewBox.
    _nodeLabelSvg(p, cx, cy, label, sub) {
      const dx = p.x - cx;
      const dy = p.y - cy;
      const horizontal = Math.abs(dx) > Math.abs(dy) * 1.5;
      const halfW = label.length * 3.0;
      const lines = [];
      if (horizontal) {
        const right = dx >= 0;
        const anchor = right ? "start" : "end";
        let x = p.x + (right ? 24 : -24);
        x = right
          ? Math.min(x, VIEW_W - 4 - 2 * halfW)
          : Math.max(x, 4 + 2 * halfW);
        const y = Math.max(14, Math.min(VIEW_H - (sub ? 18 : 6), p.y + 4));
        lines.push(
          `<text class="node-label" text-anchor="${anchor}" x="${x.toFixed(
            1
          )}" y="${y.toFixed(1)}">${this._escape(label)}</text>`
        );
        if (sub) {
          lines.push(
            `<text class="node-sub" text-anchor="${anchor}" x="${x.toFixed(
              1
            )}" y="${(y + 13).toFixed(1)}">${this._escape(sub)}</text>`
          );
        }
      } else {
        const below = dy >= 0;
        const x = Math.max(4 + halfW, Math.min(VIEW_W - 4 - halfW, p.x));
        let y = below ? p.y + 32 : p.y - (sub ? 37 : 24);
        y = Math.max(14, Math.min(VIEW_H - (sub ? 18 : 6), y));
        lines.push(
          `<text class="node-label" text-anchor="middle" x="${x.toFixed(
            1
          )}" y="${y.toFixed(1)}">${this._escape(label)}</text>`
        );
        if (sub) {
          lines.push(
            `<text class="node-sub" text-anchor="middle" x="${x.toFixed(
              1
            )}" y="${(y + 13).toFixed(1)}">${this._escape(sub)}</text>`
          );
        }
      }
      return lines.join("");
    }

    // ── Helpers ────────────────────────────────────────────

    // The adapter's base drawing dimensions (unscaled). Kept in one place so
    // the icon renderer and the in-drag DOM updater stay in sync.
    _iconScale() {
      const s = (this._userLayout && this._userLayout.iconScale) || 1;
      return Math.max(0.5, Math.min(2.5, s));
    }

    // Which adapter icon set to draw: "chatgpt" (photo, default) or "claude"
    // (the vector adapter).
    _iconStyle() {
      const style = this._userLayout && this._userLayout.iconStyle;
      return style === "claude" ? "claude" : "chatgpt";
    }

    // Where a PLC link line attaches to a node. For the Claude icon the link
    // meets the cable at the bottom of the adapter (where the powerline signal
    // physically leaves the plug); the photo icon keeps the node centre.
    _edgeAnchor(mac) {
      const p = this._positions[mac];
      if (!p) return null;
      if (this._iconStyle() === "claude") {
        return { x: p.x, y: p.y + 27 * this._iconScale() };
      }
      return p;
    }

    _adapterMetrics(p) {
      const s = this._iconScale();
      return {
        auraCx: p.x,
        auraCy: p.y + 4 * s,
        auraRx: 30 * s,
        auraRy: 38 * s,
        photoX: p.x - 22 * s,
        photoY: p.y - 30 * s,
        photoW: 44 * s,
        photoH: 60 * s,
        rectX: p.x - 17 * s,
        rectY: p.y - 25 * s,
        rectW: 34 * s,
        rectH: 50 * s,
      };
    }

    _adapterIconSvg(p, color, led, online) {
      if (this._iconStyle() === "claude") {
        return this._adapterIconClaude(p, color, led, online);
      }
      return this._adapterIconPhoto(p, color, led, online);
    }

    // Original "ChatGPT" icon: the embedded adapter photo with a quality ring.
    _adapterIconPhoto(p, color, led, online) {
      const opacity = online ? 1 : 0.55;
      const asset = led === "on" ? ADAPTER_ASSETS.on : ADAPTER_ASSETS.off;
      const m = this._adapterMetrics(p);
      return (
        `<g class="adapter-body" opacity="${opacity}" style="color:${color}">` +
        `<ellipse class="adapter-aura" cx="${m.auraCx.toFixed(1)}" cy="${m.auraCy.toFixed(1)}" rx="${m.auraRx.toFixed(1)}" ry="${m.auraRy.toFixed(1)}"></ellipse>` +
        `<image class="adapter-photo" href="${asset}" x="${m.photoX.toFixed(1)}" y="${m.photoY.toFixed(1)}"` +
        ` width="${m.photoW.toFixed(1)}" height="${m.photoH.toFixed(1)}" preserveAspectRatio="xMidYMid meet"></image>` +
        `<rect class="adapter-quality" x="${m.rectX.toFixed(1)}" y="${m.rectY.toFixed(1)}" width="${m.rectW.toFixed(1)}" height="${m.rectH.toFixed(1)}" rx="${(8 * this._iconScale()).toFixed(1)}"></rect>` +
        `</g>`
      );
    }

    // "Claude" icon: a crisp vector powerline adapter. Being pure SVG it stays
    // sharp at any size (pairs well with the size slider) and is theme-aware —
    // the body uses the card background, the outline and glow use the link
    // quality colour (currentColor), and the LEDs reflect the LED switch state.
    _adapterIconClaude(p, color, led, online) {
      const s = this._iconScale();
      const x = p.x;
      const y = p.y;
      const opacity = online ? 1 : 0.55;
      const ledOn = led === "on";
      const n = (v) => v.toFixed(1);
      // Geometry (unscaled units around the centre), all multiplied by s.
      const bodyX = x - 16 * s;
      const bodyY = y - 24 * s;
      const bodyW = 32 * s;
      const bodyH = 44 * s;
      const bodyR = 7 * s;
      const panelX = x - 11 * s;
      const panelY = y - 19 * s;
      const panelW = 22 * s;
      const panelH = 30 * s;
      const panelR = 4 * s;
      const ledCy = y - 10.5 * s;
      const ledR = 2.1 * s;
      const ledDim = "#9aa4ad";
      const ledLit = "#5fe08a";
      const parts = [`<g class="adapter-body adapter-vector" opacity="${opacity}" style="color:${color}">`];
      // Soft coloured aura behind the adapter.
      parts.push(
        `<ellipse class="adapter-aura" cx="${n(x)}" cy="${n(y + 4 * s)}" rx="${n(24 * s)}" ry="${n(30 * s)}"></ellipse>`
      );
      // A single cable exiting the bottom centre of the adapter.
      const cableW = 4.2 * s;
      parts.push(
        `<rect x="${n(x - cableW / 2)}" y="${n(y + 16 * s)}" width="${n(cableW)}" height="${n(12 * s)}" rx="${n(cableW / 2)}" fill="#8a949c"></rect>`
      );
      // Adapter body: card-coloured fill, quality-coloured outline.
      parts.push(
        `<rect x="${n(bodyX)}" y="${n(bodyY)}" width="${n(bodyW)}" height="${n(bodyH)}" rx="${n(bodyR)}"` +
        ` fill="var(--card-background-color, #fff)" stroke="currentColor" stroke-width="${n(2.4 * s)}"></rect>`
      );
      // Front panel tinted with the quality colour.
      parts.push(
        `<rect x="${n(panelX)}" y="${n(panelY)}" width="${n(panelW)}" height="${n(panelH)}" rx="${n(panelR)}"` +
        ` fill="currentColor" opacity="0.14"></rect>`
      );
      // Three status LEDs. Lit ones get a soft glow halo.
      [-6, 0, 6].forEach((dx) => {
        const cx = x + dx * s;
        if (ledOn) {
          parts.push(
            `<circle cx="${n(cx)}" cy="${n(ledCy)}" r="${n(ledR * 2.1)}" fill="${ledLit}" opacity="0.28"></circle>`
          );
        }
        parts.push(
          `<circle cx="${n(cx)}" cy="${n(ledCy)}" r="${n(ledR)}" fill="${ledOn ? ledLit : ledDim}"></circle>`
        );
      });
      parts.push(`</g>`);
      return parts.join("");
    }

    _nodeQuality(node) {
      const edges = (this._topology && this._topology.edges) || [];
      const rank = { green: 4, yellow: 3, orange: 2, red: 1, unknown: 0 };
      let best = "unknown";
      edges.forEach((edge) => {
        if (edge.source !== node.mac && edge.destination !== node.mac) return;
        const quality = edge.link_quality || "unknown";
        if ((rank[quality] || 0) > (rank[best] || 0)) best = quality;
      });
      return best;
    }

    _adapterLedState(mac) {
      const led = this._adapterEntities(mac).find((entity) => {
        const label = this._controlLabel(entity.stateObj).toLowerCase();
        return entity.domain === "switch" && label.includes("led");
      });
      return led ? led.stateObj.state : "unknown";
    }

    _nodeName(mac) {
      const node = this._topology.nodes.find((n) => n.mac === mac);
      return node && node.name !== mac ? node.name : this._shortMac(mac);
    }

    _shortMac(mac) {
      const parts = mac.split(":");
      return parts.length === 6 ? "…" + parts.slice(3).join(":") : mac;
    }

    _formatTime(iso) {
      try {
        return new Date(iso).toLocaleTimeString();
      } catch (e) {
        return iso;
      }
    }

    // Timestamp (unix seconds) for the hover readout. Short time for the 1 h /
    // 24 h ranges, date + time for the multi-day ranges.
    _formatSampleTime(t, hours) {
      const d = new Date(t * 1000);
      try {
        if (hours && hours > 24) {
          return d.toLocaleString([], {
            day: "2-digit",
            month: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
          });
        }
        return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      } catch (e) {
        return d.toLocaleString();
      }
    }

    // ── i18n ───────────────────────────────────────────────
    // Follow Home Assistant's UI language: German for "de*", English otherwise.
    _lang() {
      const l = (this._hass && this._hass.language) || "en";
      return String(l).toLowerCase().startsWith("de") ? "de" : "en";
    }

    _t(key) {
      const lang = this._lang();
      const table = STRINGS[lang] || STRINGS.en;
      return table[key] != null ? table[key] : STRINGS.en[key] != null ? STRINGS.en[key] : key;
    }

    _roleLabel(role) {
      const key = "role_" + role;
      const label = this._t(key);
      return label === key ? role : label;
    }

    _qualityText(quality) {
      const key = "quality_" + quality;
      const label = this._t(key);
      return label === key ? this._t("quality_unknown") : label;
    }

    // Coloured plain-language quality label for the details table.
    _qualityLabelHtml(quality) {
      const color = QUALITY_COLORS[quality] || QUALITY_COLORS.unknown;
      return `<span class="quality-dot" style="--dot:${color}"></span>${this._escape(
        this._qualityText(quality)
      )}`;
    }

    // ── Adapter controls ───────────────────────────────────
    // Find this adapter's controllable Home Assistant entities (LED / power
    // saving switches, QoS selector, restart button) by matching the device
    // whose identifier is (powerline, mac), then listing its entities.
    _adapterEntities(mac) {
      const hass = this._hass;
      if (!hass || !hass.devices || !hass.entities) return [];
      const target = String(mac).toLowerCase();
      let deviceId = null;
      for (const dev of Object.values(hass.devices)) {
        const ids = dev.identifiers || [];
        if (
          ids.some(
            (pair) =>
              Array.isArray(pair) &&
              pair[0] === "powerline" &&
              String(pair[1]).toLowerCase() === target
          )
        ) {
          deviceId = dev.id;
          break;
        }
      }
      if (!deviceId) return [];
      const wanted = { switch: 0, select: 1, button: 2 };
      const result = [];
      for (const ent of Object.values(hass.entities)) {
        if (ent.device_id !== deviceId) continue;
        if (ent.hidden || ent.disabled_by) continue;
        const domain = ent.entity_id.split(".")[0];
        if (!(domain in wanted)) continue;
        const stateObj = hass.states[ent.entity_id];
        if (!stateObj) continue;
        result.push({ entity_id: ent.entity_id, domain, stateObj });
      }
      // Stable order: switches, then selector, then button.
      result.sort((a, b) => wanted[a.domain] - wanted[b.domain]);
      return result;
    }

    // Strip the adapter's device name from an entity's friendly name so the
    // control label reads "LED" rather than "Powerline 1a:2b:3c LED".
    _controlLabel(stateObj) {
      const full = (stateObj.attributes && stateObj.attributes.friendly_name) || stateObj.entity_id;
      const devId = this._hass.entities[stateObj.entity_id] &&
        this._hass.entities[stateObj.entity_id].device_id;
      const dev = devId && this._hass.devices[devId];
      const devName = dev && (dev.name_by_user || dev.name);
      if (devName && full.startsWith(devName)) {
        return full.slice(devName.length).trim() || full;
      }
      return full;
    }

    _formatState(stateObj, value) {
      if (this._hass && typeof this._hass.formatEntityState === "function") {
        try {
          return this._hass.formatEntityState(stateObj, value);
        } catch (e) {
          /* fall through */
        }
      }
      return value != null ? value : stateObj.state;
    }

    _renderAdapterControls(mac) {
      const entities = this._adapterEntities(mac);
      if (!entities.length) return "";
      const rows = entities
        .map((e) => {
          const label = this._escape(this._controlLabel(e.stateObj));
          const unavailable =
            e.stateObj.state === "unavailable" || e.stateObj.state === "unknown";
          const rowCls = unavailable ? "control-row unavailable" : "control-row";
          if (e.domain === "switch") {
            const on = e.stateObj.state === "on";
            return (
              `<div class="${rowCls}"><span class="label">${label}</span>` +
              `<button class="toggle" role="switch" data-toggle="${this._escape(
                e.entity_id
              )}" aria-checked="${on}"${unavailable ? " disabled" : ""}></button></div>`
            );
          }
          if (e.domain === "select") {
            const options = (e.stateObj.attributes && e.stateObj.attributes.options) || [];
            const opts = options
              .map(
                (o) =>
                  `<option value="${this._escape(o)}"${
                    o === e.stateObj.state ? " selected" : ""
                  }>${this._escape(this._formatState(e.stateObj, o))}</option>`
              )
              .join("");
            return (
              `<div class="${rowCls}"><span class="label">${label}</span>` +
              `<select class="control-select" data-select="${this._escape(
                e.entity_id
              )}"${unavailable ? " disabled" : ""}>${opts}</select></div>`
            );
          }
          // button
          return (
            `<div class="${rowCls}"><span class="label">${label}</span>` +
            `<button class="control-btn" data-press="${this._escape(
              e.entity_id
            )}"${unavailable ? " disabled" : ""}>${this._escape(this._t("run"))}</button></div>`
          );
        })
        .join("");
      return `<div class="controls"><div class="controls-title">${this._escape(this._t("settings"))}</div>${rows}</div>`;
    }

    _escape(text) {
      return String(text)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }
  }

  if (!customElements.get("powerline-topology-card")) {
    customElements.define("powerline-topology-card", PowerlineTopologyCard);
  }

  window.customCards = window.customCards || [];
  if (!window.customCards.some((c) => c.type === "powerline-topology-card")) {
    window.customCards.push({
      type: "powerline-topology-card",
      name: "Powerline Topology Card",
      description:
        "Live mesh graph of your powerline adapters and PLC link rates.",
      preview: false,
    });
  }
})();
