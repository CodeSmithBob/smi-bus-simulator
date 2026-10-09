/* SVG facade: one window per simulated SMI drive, animated from live state. */
(function () {
  "use strict";

  const NS = "http://www.w3.org/2000/svg";
  const CELL_W = 200;
  const CELL_H = 236;
  const PAD = 24;
  const ROOF = 34;
  const GLASS = { x: 30, y: 40, w: 140, h: 148 };
  const SLAT_PITCH = 8;
  const MAX_SLATS = Math.ceil(GLASS.h / SLAT_PITCH) + 1;

  function el(name, attrs, parent) {
    const node = document.createElementNS(NS, name);
    if (attrs) for (const k in attrs) node.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(node);
    return node;
  }

  function defs(svg) {
    const d = el("defs", null, svg);
    const sky = el("linearGradient", { id: "g-sky", x1: 0, y1: 0, x2: 0, y2: 1 }, d);
    el("stop", { offset: "0", class: "sky-top" }, sky);
    el("stop", { offset: "1", class: "sky-bottom" }, sky);

    const roller = el("pattern", { id: "p-roller", width: 8, height: 7, patternUnits: "userSpaceOnUse" }, d);
    el("rect", { width: 8, height: 7, class: "roller-a" }, roller);
    el("rect", { y: 5.5, width: 8, height: 1.5, class: "roller-b" }, roller);

    const fabric = el("pattern", { id: "p-fabric", width: 4, height: 4, patternUnits: "userSpaceOnUse" }, d);
    el("rect", { width: 4, height: 4, class: "fabric-a" }, fabric);
    el("rect", { width: 2, height: 2, class: "fabric-b" }, fabric);
    el("rect", { x: 2, y: 2, width: 2, height: 2, class: "fabric-b" }, fabric);

    const stripes = el("pattern", {
      id: "p-awning", width: 20, height: 10, patternUnits: "userSpaceOnUse",
    }, d);
    el("rect", { width: 20, height: 10, class: "awning-a" }, stripes);
    el("rect", { width: 10, height: 10, class: "awning-b" }, stripes);

    const wall = el("pattern", { id: "p-wall", width: 40, height: 20, patternUnits: "userSpaceOnUse" }, d);
    el("rect", { width: 40, height: 20, class: "wall-a" }, wall);
    el("path", { d: "M0 19.5H40M20 0V10M0 9.5H40M40 10V20M0 10V20", class: "wall-joint" }, wall);
  }

  /* Side cross-section through the window: shows slat angle (venetian), roll diameter
     (roller/screen) or arm reach (awning), plus how much daylight reaches the room.
     Outside is on the left, the room on the right. */
  const SEC_SLATS = 18;

  class Section {
    constructor(parent, x, y, w, h) {
      this.w = w;
      this.h = h;
      this.gx = Math.round(w * 0.74); // glass plane
      this.cx = Math.round(this.gx * 0.48); // blind plane
      this.top = 12;
      this.H = h - 24;
      const g = (this.g = el("g", { transform: `translate(${x} ${y})`, class: "section" }, parent));
      el("rect", { x: 0, y: 0, width: w, height: h, rx: 3, class: "sec-bg" }, g);
      this.light = el("path", { class: "sec-light" }, g);
      el("rect", { x: this.gx + 3, y: 0, width: w - this.gx - 3, height: 12, class: "sec-wall" }, g);
      el("rect", { x: this.gx + 3, y: h - 12, width: w - this.gx - 3, height: 12, class: "sec-wall" }, g);
      el("rect", { x: this.gx - 1, y: 12, width: 3, height: h - 24, class: "sec-glass" }, g);
      this.box = el("rect", { x: 2, y: 0, width: this.gx - 5, height: 12, rx: 2, class: "sec-box" }, g);
      el("text", { x: 3, y: 21, class: "sec-tag" }, g).textContent = "out";
      el("text", { x: w - 2, y: 21, "text-anchor": "end", class: "sec-tag" }, g).textContent = "in";
      this.dyn = el("g", null, g);
      this.kind = null;
    }

    build(kind) {
      this.kind = kind;
      this.dyn.textContent = "";
      this.slats = [];
      if (kind === "venetian") {
        this.cordOut = el("polyline", { class: "sec-cord" }, this.dyn);
        this.cordIn = el("polyline", { class: "sec-cord" }, this.dyn);
        this.stack = el("rect", { x: this.cx - 9, y: this.top, width: 18, height: 0, rx: 1, class: "sec-stack" }, this.dyn);
        for (let i = 0; i < SEC_SLATS; i++) this.slats.push(el("path", { class: "sec-slat" }, this.dyn));
        this.bar = el("rect", { width: 14, height: 4, rx: 1, class: "sec-bar" }, this.dyn);
      } else if (kind === "awning") {
        this.arm = el("line", { class: "sec-arm" }, this.dyn);
        this.cloth = el("line", { class: "sec-cloth awning" }, this.dyn);
        this.roll = el("circle", { class: "sec-roll" }, this.dyn);
        this.bar = el("rect", { width: 6, height: 4, rx: 1, class: "sec-bar" }, this.dyn);
      } else {
        this.cloth = el("line", { class: `sec-cloth ${kind}` }, this.dyn);
        this.roll = el("circle", { class: "sec-roll" }, this.dyn);
        this.bar = el("rect", { width: 8, height: 4, rx: 1, class: "sec-bar" }, this.dyn);
      }
      this.box.style.display = kind === "awning" ? "none" : "";
    }

    draw(kind, p, slatDeg, daylight) {
      if (kind !== this.kind) this.build(kind);
      const { cx, top, H, gx, h, w } = this;
      // daylight patch on the room side
      const reach = 6 + daylight * (w - gx - 6);
      this.light.setAttribute("d", `M${gx + 2} ${top} L${gx + 2 + reach} ${h - 12} L${gx + 2} ${h - 12} Z`);
      this.light.style.opacity = (0.12 + 0.6 * daylight).toFixed(3);
      if (kind === "venetian") return this.drawSlats(p, slatDeg);
      if (kind === "awning") return this.drawAwning(p);
      const r = 2 + (1 - p) * 3.5; // more fabric wound up -> bigger roll
      const end = top + p * H;
      this.roll.setAttribute("cx", cx);
      this.roll.setAttribute("cy", 6);
      this.roll.setAttribute("r", r.toFixed(2));
      this.cloth.setAttribute("x1", cx + r);
      this.cloth.setAttribute("y1", 6);
      this.cloth.setAttribute("x2", cx + r);
      this.cloth.setAttribute("y2", end.toFixed(2));
      this.bar.setAttribute("x", (cx + r - 4).toFixed(2));
      this.bar.setAttribute("y", (end - 1).toFixed(2));
      this.bar.style.display = p > 0.01 ? "" : "none";
      this.cloth.style.display = p > 0.01 ? "" : "none";
    }

    drawSlats(p, slatDeg) {
      const { cx, top, H } = this;
      const pitch = H / SEC_SLATS;
      const visible = Math.min(SEC_SLATS, Math.floor((p * H) / pitch + 1e-6));
      const stackH = (SEC_SLATS - visible) * 1.2;
      const L = Math.min(pitch * 2.1, this.gx * 0.7); // slat depth: overlaps when closed
      const t = (slatDeg * Math.PI) / 180;
      const dx = (L / 2) * Math.cos(t);
      const dy = (L / 2) * Math.sin(t);
      const nx = -Math.sin(t) * L * 0.13; // curvature: slats bulge upwards
      const ny = -Math.cos(t) * L * 0.13;
      this.stack.setAttribute("height", stackH.toFixed(2));
      this.stack.style.display = stackH > 0.5 ? "" : "none";
      const y0 = top + stackH;
      const outer = [];
      const inner = [];
      for (let i = 0; i < this.slats.length; i++) {
        const s = this.slats[i];
        if (i >= visible) { s.style.display = "none"; continue; }
        const cy = y0 + i * pitch + pitch / 2;
        const ox = cx - dx, oy = cy + dy, ix = cx + dx, iy = cy - dy;
        s.setAttribute("d", `M${ox.toFixed(2)} ${oy.toFixed(2)} Q${(cx + nx).toFixed(2)} ${(cy + ny).toFixed(2)} ${ix.toFixed(2)} ${iy.toFixed(2)}`);
        s.style.display = "";
        outer.push(`${ox.toFixed(1)},${oy.toFixed(1)}`);
        inner.push(`${ix.toFixed(1)},${iy.toFixed(1)}`);
      }
      const barY = y0 + visible * pitch;
      this.bar.setAttribute("x", cx - 7);
      this.bar.setAttribute("y", barY.toFixed(2));
      outer.unshift(`${cx - 4},${top}`);
      inner.unshift(`${cx + 4},${top}`);
      outer.push(`${cx - 4},${barY.toFixed(1)}`);
      inner.push(`${cx + 4},${barY.toFixed(1)}`);
      this.cordOut.setAttribute("points", outer.join(" "));
      this.cordIn.setAttribute("points", inner.join(" "));
    }

    drawAwning(p) {
      const { gx, h } = this;
      const sx = gx - 2, sy = 14;
      const ex = sx - 6 - p * (gx - 10);
      const ey = sy + 4 + p * h * 0.28;
      this.roll.setAttribute("cx", sx - 3);
      this.roll.setAttribute("cy", 8);
      this.roll.setAttribute("r", (2 + (1 - p) * 2.5).toFixed(2));
      this.arm.setAttribute("x1", sx);
      this.arm.setAttribute("y1", sy + 10);
      this.arm.setAttribute("x2", ex.toFixed(2));
      this.arm.setAttribute("y2", ey.toFixed(2));
      this.cloth.setAttribute("x1", sx - 3);
      this.cloth.setAttribute("y1", 8);
      this.cloth.setAttribute("x2", ex.toFixed(2));
      this.cloth.setAttribute("y2", ey.toFixed(2));
      this.bar.setAttribute("x", (ex - 3).toFixed(2));
      this.bar.setAttribute("y", (ey - 2).toFixed(2));
    }
  }

  class Window {
    constructor(parent, motor, x, y, onSelect, opts = {}) {
      this.opts = opts;
      const wide = !!opts.section;
      const midX = wide ? 146 : 100;
      this.uid = motor.uid;
      this.kind = motor.kind;
      this.shown = { pos: motor.position / 65535, tilt: motor.tilt };
      this.target = { pos: this.shown.pos, tilt: this.shown.tilt };
      const g = (this.g = el("g", { transform: `translate(${x} ${y})`, class: "window" }, parent));
      if (onSelect) {
        g.setAttribute("tabindex", "0");
        g.setAttribute("role", "button");
        g.addEventListener("click", () => onSelect(this.uid));
        g.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onSelect(this.uid);
          }
        });
      } else {
        g.classList.add("static");
      }

      el("rect", { x: 22, y: 30, width: 156, height: 168, rx: 4, class: "win-frame" }, g);
      const clipId = `clip-${opts.prefix || "f"}-${motor.uid}`;
      const cp = el("clipPath", { id: clipId }, g);
      el("rect", { x: GLASS.x, y: GLASS.y, width: GLASS.w, height: GLASS.h }, cp);
      const glass = el("g", { "clip-path": `url(#${clipId})` }, g);
      el("rect", { x: GLASS.x, y: GLASS.y, width: GLASS.w, height: GLASS.h, fill: "url(#g-sky)" }, glass);
      el("rect", { x: GLASS.x, y: GLASS.y + GLASS.h * 0.72, width: GLASS.w, height: GLASS.h * 0.28, class: "room" }, glass);
      el("path", {
        d: `M${GLASS.x + 18} ${GLASS.y} l40 0 l-60 ${GLASS.h} l-40 0z`, class: "reflection",
      }, glass);
      el("rect", { x: GLASS.x + GLASS.w / 2 - 1, y: GLASS.y, width: 2, height: GLASS.h, class: "mullion" }, glass);
      this.shadow = el("rect", { x: GLASS.x, y: GLASS.y, width: GLASS.w, height: 0, class: "awning-shadow" }, glass);

      this.blind = el("g", { class: "blind" }, glass);
      this.buildBlind(motor.kind);

      el("rect", { x: 16, y: 196, width: 168, height: 6, rx: 2, class: "sill" }, g);
      this.cassette = el("rect", { x: 24, y: 30, width: 152, height: 11, rx: 2, class: "cassette" }, g);
      this.awning = el("g", { class: "awning" }, g);
      this.awningBody = el("path", { fill: "url(#p-awning)", class: "awning-body" }, this.awning);
      this.awningBar = el("rect", { height: 4, rx: 2, class: "awning-bar" }, this.awning);

      this.badge = el("g", { class: "badge-addr" }, g);
      el("circle", { cx: 24, cy: 26, r: 12 }, this.badge);
      this.badgeText = el("text", { x: 24, y: 30, "text-anchor": "middle" }, this.badge);

      this.arrow = el("path", { class: "dir-arrow", d: "" }, g);
      this.alert = el("g", { class: "alert-mark" }, g);
      el("path", { d: "M168 14 l11 19 h-22z" }, this.alert);
      el("text", { x: 168, y: 30, "text-anchor": "middle" }, this.alert).textContent = "!";

      this.section = wide ? new Section(g, 196, 30, 92, 168) : null;
      this.name = el("text", { x: midX, y: 218, "text-anchor": "middle", class: "win-name" }, g);
      this.info = el("text", { x: midX, y: 232, "text-anchor": "middle", class: "win-info" }, g);
      this.heat = el("rect", { x: 22, y: 202, width: 0, height: 3, rx: 1.5, class: "heat" }, g);
      this.heatW = wide ? 266 : 156;

      const ringW = wide ? 284 : 172;
      this.ring = el("rect", { x: 14, y: 8, width: ringW, height: 230, rx: 10, class: "select-ring" }, g);
      this.wink = el("rect", { x: 18, y: 26, width: 164, height: 176, rx: 6, class: "wink-ring" }, g);
    }

    buildBlind(kind) {
      this.blind.textContent = "";
      this.kind = kind;
      this.slats = [];
      this.curtain = this.rail = this.stack = null;
      if (kind === "venetian") {
        this.stack = el("rect", { x: GLASS.x, y: GLASS.y, width: GLASS.w, height: 0, class: "slat-stack" }, this.blind);
        for (let i = 0; i < MAX_SLATS; i++) {
          this.slats.push(el("rect", { x: GLASS.x, width: GLASS.w, height: 2, class: "slat" }, this.blind));
        }
        this.rail = el("rect", { x: GLASS.x, width: GLASS.w, height: 3, class: "rail" }, this.blind);
      } else if (kind === "roller" || kind === "screen") {
        const fill = kind === "roller" ? "url(#p-roller)" : "url(#p-fabric)";
        this.curtain = el("rect", { x: GLASS.x, y: GLASS.y, width: GLASS.w, height: 0, fill, class: `curtain ${kind}` }, this.blind);
        this.rail = el("rect", { x: GLASS.x, width: GLASS.w, height: kind === "roller" ? 4 : 3, class: "rail" }, this.blind);
      }
    }

    setState(m, selected, duplicate) {
      if (m.kind !== this.kind) this.buildBlind(m.kind);
      this.target.pos = m.position / 65535;
      this.target.tilt = m.tilt;
      this.slatRange = [m.config.slat_min_deg, m.config.slat_max_deg];
      this.daylight = m.daylight;
      this.state = m;
      this.g.setAttribute("aria-label", `${m.name || "drive"}, address ${m.address}, ${m.percent} percent`);
      this.g.classList.toggle("selected", selected);
      this.g.classList.toggle("moving", m.direction !== 0);
      this.g.classList.toggle("has-error", m.errors.length > 0);
      this.g.classList.toggle("offline", m.faults.includes("offline"));
      this.g.classList.toggle("winking", m.wink);
      this.g.classList.toggle("calibrating", m.calibrating);
      this.badge.classList.toggle("duplicate", duplicate);
      this.badgeText.textContent = String(m.address);
      this.name.textContent = m.name || `Drive ${m.uid}`;
      let info = `${Math.round(m.percent)} %`;
      if (m.kind === "venetian") info += ` · slats ${Math.round(m.slat_percent)} %`;
      if (m.calibrating) info = "calibrating…";
      else if (!m.limits_set) info += " · no limits";
      if (m.errors.length) info += ` · ${m.errors.join(", ")}`;
      this.info.textContent = info;
      this.heat.setAttribute("width", String(this.heatW * m.heat));
      this.heat.classList.toggle("hot", m.heat > 0.75);
      const d = m.direction;
      this.arrow.setAttribute("d", d < 0 ? "M168 14 l9 12 h-18z" : d > 0 ? "M168 34 l9 -12 h-18z" : "");
      this.cassette.style.display = m.kind === "awning" ? "none" : "";
      this.awning.style.display = m.kind === "awning" ? "" : "none";
      this.shadow.style.display = m.kind === "awning" ? "" : "none";
    }

    frame(dt) {
      const k = Math.min(1, dt * 14);
      this.shown.pos += (this.target.pos - this.shown.pos) * k;
      this.shown.tilt += (this.target.tilt - this.shown.tilt) * k;
      const p = this.shown.pos;
      const [lo, hi] = this.slatRange || [-80, 80];
      const slatDeg = lo + this.shown.tilt * (hi - lo);
      if (this.kind === "venetian") this.drawVenetian(p, slatDeg);
      else if (this.kind === "awning") this.drawAwning(p);
      else this.drawCurtain(p);
      if (this.section) this.section.draw(this.kind, p, slatDeg, this.daylight ?? 1);
    }

    drawCurtain(p) {
      const h = Math.max(0, p * GLASS.h);
      this.curtain.setAttribute("height", h.toFixed(2));
      this.rail.setAttribute("y", (GLASS.y + h - 2).toFixed(2));
      this.rail.style.display = h > 0.5 ? "" : "none";
    }

    drawVenetian(p, slatDeg) {
      const length = p * GLASS.h;
      const visible = Math.floor(length / SLAT_PITCH);
      const stackH = Math.max(0, (MAX_SLATS - visible) * 0.9 - 1);
      const theta = (slatDeg * Math.PI) / 180;
      const extent = Math.max(1, SLAT_PITCH * 1.12 * Math.abs(Math.sin(theta)) + 0.8);
      this.stack.setAttribute("height", stackH.toFixed(2));
      this.stack.style.display = stackH > 0.5 ? "" : "none";
      const top = GLASS.y + stackH;
      const up = theta < 0;
      for (let i = 0; i < this.slats.length; i++) {
        const s = this.slats[i];
        if (i < visible) {
          const cy = top + i * SLAT_PITCH + SLAT_PITCH / 2;
          s.setAttribute("y", (cy - extent / 2).toFixed(2));
          s.setAttribute("height", extent.toFixed(2));
          s.style.display = "";
          s.classList.toggle("up", up);
        } else {
          s.style.display = "none";
        }
      }
      const railY = top + visible * SLAT_PITCH;
      this.rail.setAttribute("y", Math.min(GLASS.y + GLASS.h - 3, railY).toFixed(2));
    }

    drawAwning(p) {
      const reach = 8 + p * 92;
      const spread = p * 14;
      const top = 30;
      const bottom = top + reach;
      this.awningBody.setAttribute(
        "d",
        `M24 ${top} H176 L${176 + spread} ${bottom} H${24 - spread} Z`
      );
      this.awningBar.setAttribute("x", String(22 - spread));
      this.awningBar.setAttribute("y", String(bottom - 2));
      this.awningBar.setAttribute("width", String(156 + spread * 2));
      this.shadow.setAttribute("height", String(Math.max(0, p * GLASS.h * 0.9)));
    }
  }

  class Line {
    constructor(container, onSelect) {
      this.container = container;
      this.onSelect = onSelect;
      this.mode = "front";
      this.signature = "";
      this.windows = new Map();
      this.svg = null;
    }

    build(bus) {
      const n = Math.max(bus.motors.length, 1);
      const cols = Math.min(n, this.columns());
      const rows = Math.ceil(n / cols);
      const cellW = this.cellW();
      const width = cols * cellW + PAD * 2;
      const height = rows * CELL_H + PAD + ROOF;
      this.container.textContent = "";
      const svg = (this.svg = el("svg", {
        viewBox: `0 0 ${width} ${height}`, class: "facade", role: "group",
        "aria-label": `${bus.settings.name} facade`,
      }, this.container));
      defs(svg);
      el("rect", { x: 0, y: ROOF - 6, width, height: height - ROOF + 6, fill: "url(#p-wall)", class: "wall" }, svg);
      el("path", { d: `M-4 ${ROOF - 4} L${width / 2} 6 L${width + 4} ${ROOF - 4} Z`, class: "roof" }, svg);
      for (let r = 1; r < rows; r++) {
        el("rect", { x: 0, y: ROOF + r * CELL_H - 4, width, height: 4, class: "slab" }, svg);
      }
      this.windows.clear();
      bus.motors.forEach((m, i) => {
        const x = PAD + (i % cols) * cellW;
        const y = ROOF + Math.floor(i / cols) * CELL_H;
        this.windows.set(m.uid, new Window(svg, m, x, y, this.onSelect, { section: this.mode === "both" }));
      });
      if (!bus.motors.length) {
        el("text", { x: width / 2, y: height / 2, "text-anchor": "middle", class: "empty-line" }, svg)
          .textContent = "No drives on this line";
      }
    }

    cellW() {
      return this.mode === "both" ? 296 : CELL_W;
    }

    columns() {
      const w = this.container.clientWidth || 800;
      if (this.mode === "both") return w < 560 ? 2 : w < 1000 ? 3 : 5;
      return w < 560 ? 4 : w < 1000 ? 6 : 8;
    }

    update(bus, selected, mode) {
      if (mode) this.mode = mode;
      const sig = this.mode + this.columns() + ":" + bus.motors.map((m) => m.uid).join(",");
      if (sig !== this.signature) {
        this.signature = sig;
        this.build(bus);
      }
      const dups = new Set(bus.duplicates);
      for (const m of bus.motors) {
        const w = this.windows.get(m.uid);
        if (w) w.setState(m, m.uid === selected, dups.has(m.address));
      }
    }

    frame(dt) {
      for (const w of this.windows.values()) w.frame(dt);
    }
  }

  /* Large front view + section of one drive, for the drive panel. */
  class Detail {
    constructor(container, motor) {
      container.textContent = "";
      const svg = el("svg", { viewBox: "8 4 288 238", class: "detail-svg", role: "img" }, container);
      svg.setAttribute("aria-label", "Front view and side section of the selected drive");
      defs(svg);
      el("rect", { x: 8, y: 4, width: 288, height: 238, rx: 8, fill: "url(#p-wall)", class: "wall" }, svg);
      this.win = new Window(svg, motor, 0, 0, null, { section: true, prefix: "d" });
      this.uid = motor.uid;
    }

    update(m) {
      this.win.setState(m, false, false);
    }

    frame(dt) {
      this.win.frame(dt);
    }
  }

  window.Facade = { Line, Detail };
})();
