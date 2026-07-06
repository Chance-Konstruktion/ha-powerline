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

  const VIEW_W = 600;
  const VIEW_H = 400;

  class PowerlineTopologyCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._topology = null;
      this._positions = {}; // mac -> {x, y}
      this._selected = null; // {kind: "node"|"edge", id}
      this._timer = null;
      this._lastFetch = 0;
      this._fetchInFlight = false;
      this._error = null;
    }

    setConfig(config) {
      this._config = {
        title: config.title || "Powerline Mesh",
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
          pos[m].x = Math.max(45, Math.min(VIEW_W - 45, pos[m].x));
          pos[m].y = Math.max(40, Math.min(VIEW_H - 45, pos[m].y));
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
        }
        .node { cursor: pointer; }
        .node-label {
          font: 12px sans-serif;
          fill: var(--primary-text-color, #212121);
          text-anchor: middle;
          pointer-events: none;
        }
        .node-sub {
          font: 10px sans-serif;
          fill: var(--secondary-text-color, #666);
          text-anchor: middle;
          pointer-events: none;
        }
        .selected-ring { fill: none; stroke: var(--primary-color, #03a9f4); stroke-width: 2.5; }
        .cco-ring { fill: none; stroke: var(--primary-color, #03a9f4); stroke-width: 1.5; stroke-dasharray: 3 2; }
        .details {
          border-top: 1px solid var(--divider-color, #e0e0e0);
          padding: 8px 16px 12px;
          font-size: 0.9em;
          color: var(--primary-text-color);
        }
        .details table { border-collapse: collapse; width: 100%; }
        .details td { padding: 2px 8px 2px 0; vertical-align: top; }
        .details td:first-child { color: var(--secondary-text-color); white-space: nowrap; }
        .hint {
          padding: 4px 16px 12px;
          font-size: 0.8em;
          color: var(--secondary-text-color);
        }
        .error { padding: 16px; color: var(--error-color, #e53935); }
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
      `;

      let body;
      if (this._error) {
        body = `<div class="error">Powerline topology: ${this._escape(
          this._error
        )}</div>`;
      } else if (!this._topology || !this._topology.nodes.length) {
        body = `<div class="empty">No powerline adapters discovered yet.</div>`;
      } else {
        body = `<div class="graph">${this._renderSvg()}</div>${this._renderLegend()}${this._renderDetails()}`;
      }

      this.shadowRoot.innerHTML = `
        <style>${style}</style>
        <ha-card>
          <div class="header">${this._escape(this._config.title)}</div>
          ${body}
        </ha-card>
      `;
      this._bindEvents();
    }

    _renderSvg() {
      const t = this._topology;
      const parts = [];
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
        const mx = (a.x + b.x) / 2;
        const my = (a.y + b.y) / 2 - 6;
        if (e.average_rate > 0) {
          parts.push(
            `<text class="edge-label" x="${mx}" y="${my}">${e.average_rate} Mbit/s${
              e.estimated ? " ~" : ""
            }</text>`
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
        parts.push(
          `<text class="node-label" x="${p.x}" y="${p.y + 30}">${this._escape(
            label
          )}</text>`
        );
        if (node.role === "CCo") {
          parts.push(
            `<text class="node-sub" x="${p.x}" y="${p.y + 43}">CCo</text>`
          );
        }
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
        ? `<span style="--dot:transparent">gestrichelt = geschätzt</span>`
        : "";
      return `<div class="legend">${items}${estimated}</div>`;
    }

    _renderDetails() {
      if (!this._selected) {
        return `<div class="hint">Adapter oder Verbindung anklicken für Details.</div>`;
      }
      const rows = [];
      if (this._selected.kind === "node") {
        const node = this._topology.nodes.find(
          (n) => n.mac === this._selected.id
        );
        if (!node) return "";
        rows.push(["Name", node.name]);
        rows.push(["MAC", node.mac]);
        if (node.model) rows.push(["Modell", node.model]);
        if (node.firmware) rows.push(["Firmware", node.firmware]);
        if (node.manufacturer) rows.push(["Hersteller", node.manufacturer]);
        if (node.chipset) rows.push(["Chipsatz", node.chipset]);
        rows.push(["Rolle", node.role]);
        rows.push(["Status", node.online ? "online" : "offline"]);
        rows.push(["Letztes Update", this._formatTime(node.last_update)]);
      } else {
        const edge = this._topology.edges[this._selected.id];
        if (!edge) return "";
        rows.push([
          "Verbindung",
          `${this._nodeName(edge.source)} ↔ ${this._nodeName(edge.destination)}`,
        ]);
        rows.push(["TX", `${edge.tx_phy_rate} Mbit/s`]);
        rows.push(["RX", `${edge.rx_phy_rate} Mbit/s`]);
        rows.push(["Durchschnitt", `${edge.average_rate} Mbit/s`]);
        rows.push(["Qualität", edge.link_quality]);
        if (edge.estimated) rows.push(["Hinweis", "geschätzt (keine paarweise Messung)"]);
        rows.push(["Letztes Update", this._formatTime(edge.timestamp)]);
      }
      const table = rows
        .map(
          ([k, v]) =>
            `<tr><td>${this._escape(k)}</td><td>${this._escape(String(v))}</td></tr>`
        )
        .join("");
      return `<div class="details"><table>${table}</table></div>`;
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
          this._selected =
            this._selected &&
            this._selected.kind === "edge" &&
            this._selected.id === idx
              ? null
              : { kind: "edge", id: idx };
          this._render();
        });
      });
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
