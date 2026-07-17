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
    },
  };

  const VIEW_W = 600;
  const VIEW_H = 400;

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
      this._render();
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
        .graph { width: 100%; display: block; }
        svg { width: 100%; height: auto; display: block; }
        .edge { cursor: pointer; }
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
        body = `<div class="graph">${this._renderSvg()}</div>${this._renderLegend()}${this._renderAnalysis()}${this._renderDetails()}`;
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
        `<svg viewBox="0 0 ${VIEW_W} ${VIEW_H}" preserveAspectRatio="xMidYMid meet">`
      );

      // Edges below nodes
      t.edges.forEach((e, i) => {
        const a = this._positions[e.source];
        const b = this._positions[e.destination];
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
        const fill = node.online
          ? QUALITY_COLORS.green
          : QUALITY_COLORS.red;
        const sel =
          this._selected &&
          this._selected.kind === "node" &&
          this._selected.id === node.mac;
        parts.push(`<g class="node" data-node="${this._escape(node.mac)}">`);
        if (node.role === "CCo") {
          parts.push(`<circle class="cco-ring" cx="${p.x}" cy="${p.y}" r="19"></circle>`);
        }
        if (sel) {
          parts.push(`<circle class="selected-ring" cx="${p.x}" cy="${p.y}" r="23"></circle>`);
        }
        parts.push(
          `<circle cx="${p.x}" cy="${p.y}" r="14" fill="${fill}"` +
            ` stroke="var(--card-background-color, #fff)" stroke-width="2"></circle>`
        );
        const label = node.name === node.mac ? this._shortMac(node.mac) : node.name;
        const sub = node.role === "CCo" ? this._t("badge_CCo") : "";
        parts.push(this._nodeLabelSvg(p, cx, cy, label, sub));
        parts.push(`</g>`);
      });

      parts.push(`</svg>`);
      return parts.join("");
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

      const line = series.map((p) => `${x(p.t).toFixed(1)},${y(p.avg).toFixed(1)}`).join(" ");
      let band = "";
      if (series.some((p) => p.min != null)) {
        const upper = series.map((p) => `${x(p.t).toFixed(1)},${y(p.max != null ? p.max : p.avg).toFixed(1)}`);
        const lower = series
          .slice()
          .reverse()
          .map((p) => `${x(p.t).toFixed(1)},${y(p.min != null ? p.min : p.avg).toFixed(1)}`);
        band = `<polygon points="${upper.join(" ")} ${lower.join(" ")}" fill="var(--primary-color, #03a9f4)" opacity="0.15"></polygon>`;
      }
      const last = series[series.length - 1];
      // Default readout (no hover) = maximum over the visible range. The
      // crosshair line uses a non-scaling stroke so it stays 1px despite the
      // SVG's horizontal stretch; the dot is an HTML overlay for the same
      // reason. Both are hidden until the pointer enters the chart.
      const svg =
        `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">` +
        band +
        `<polyline points="${line}" fill="none" stroke="var(--primary-color, #03a9f4)" stroke-width="1.5"></polyline>` +
        `<circle cx="${x(last.t).toFixed(1)}" cy="${y(last.avg).toFixed(1)}" r="2.5" fill="var(--primary-color, #03a9f4)"></circle>` +
        `<line class="spark-cursor-line" x1="0" y1="0" x2="0" y2="${H}"` +
        ` stroke="var(--secondary-text-color, #666)" stroke-width="1"` +
        ` vector-effect="non-scaling-stroke" opacity="0" pointer-events="none"></line>` +
        `</svg>`;
      const defaultLabel = `Max ${hi} Mbit/s`;
      return (
        `<div class="spark-wrap">` +
        `<div class="spark-readout" data-default="${this._escape(defaultLabel)}">${this._escape(defaultLabel)}</div>` +
        svg +
        `<div class="spark-dot"></div>` +
        `</div>`
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
