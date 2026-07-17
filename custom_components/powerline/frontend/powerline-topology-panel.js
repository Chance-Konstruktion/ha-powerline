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
      /* No toolbar: the graph gets the full frame. The hamburger floats
         on top so the sidebar stays reachable on narrow screens. */
      .menu {
        position: absolute;
        top: 8px;
        left: 8px;
        z-index: 1;
        color: var(--primary-text-color);
      }
      .content {
        position: relative;
        min-height: 100%;
        padding: 8px;
        box-sizing: border-box;
      }
      .content > powerline-topology-card {
        display: block;
        max-width: 1400px;
        margin: 0 auto;
      }
    `;

    const content = document.createElement("div");
    content.className = "content";

    // ha-menu-button renders only on narrow screens; floated over the card
    // so no header bar is needed.
    this._menuButton = document.createElement("ha-menu-button");
    this._menuButton.className = "menu";
    if (this._hass) this._menuButton.hass = this._hass;
    this._menuButton.narrow = !!this._narrow;

    this._card = document.createElement("powerline-topology-card");
    this._card.setConfig({ title: "", refresh_interval: 15 });
    content.append(this._menuButton, this._card);

    this.shadowRoot.append(style, content);
  }
}

if (!customElements.get("powerline-topology-panel")) {
  customElements.define("powerline-topology-panel", PowerlineTopologyPanel);
}
