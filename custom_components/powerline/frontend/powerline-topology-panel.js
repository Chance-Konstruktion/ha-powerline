/* Powerline Topology Panel
 *
 * Sidebar panel wrapper around <powerline-topology-card>: full-page view of
 * the mesh graph with its own toolbar. Registered by the integration as a
 * custom panel ("Powerline" in the sidebar); can be disabled in the
 * integration options.
 */

import "./powerline-topology-card.js";

class PowerlineTopologyPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._card = null;
    this._menuButton = null;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._card) this._build();
    this._card.hass = hass;
    if (this._menuButton) this._menuButton.hass = hass;
  }

  set narrow(narrow) {
    this._narrow = narrow;
    if (this._menuButton) this._menuButton.narrow = narrow;
  }

  set route(route) {}
  set panel(panel) {}

  _build() {
    const style = document.createElement("style");
    style.textContent = `
      :host {
        display: block;
        height: 100%;
        overflow-y: auto;
        background: var(--primary-background-color);
      }
      .toolbar {
        display: flex;
        align-items: center;
        gap: 8px;
        height: 56px;
        padding: 0 16px;
        background: var(--app-header-background-color, var(--primary-color));
        color: var(--app-header-text-color, #fff);
        font-size: 20px;
        font-weight: 400;
      }
      .content {
        max-width: 920px;
        margin: 0 auto;
        padding: 16px;
        box-sizing: border-box;
      }
    `;

    const toolbar = document.createElement("div");
    toolbar.className = "toolbar";
    // ha-menu-button renders the hamburger on narrow screens so the
    // sidebar stays reachable from this panel on mobile.
    this._menuButton = document.createElement("ha-menu-button");
    if (this._hass) this._menuButton.hass = this._hass;
    this._menuButton.narrow = !!this._narrow;
    const title = document.createElement("div");
    title.textContent = "Powerline";
    toolbar.append(this._menuButton, title);

    const content = document.createElement("div");
    content.className = "content";
    this._card = document.createElement("powerline-topology-card");
    this._card.setConfig({ title: "Powerline Mesh", refresh_interval: 15 });
    content.appendChild(this._card);

    this.shadowRoot.append(style, toolbar, content);
  }
}

if (!customElements.get("powerline-topology-panel")) {
  customElements.define("powerline-topology-panel", PowerlineTopologyPanel);
}
