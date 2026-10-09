/* SMI Bus Simulator web UI. Plain JavaScript, no build step. */
(function () {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const CODES = {
    command: [
      ["up", "UP"], ["down", "DOWN"], ["stop", "STOP"], ["goto", "GOTO position"],
      ["pos1", "POS1 (recall / store)"], ["pos2", "POS2 (recall / store)"],
    ],
    query: [
      ["position", "Position"], ["pos1", "Stored POS1"], ["pos2", "Stored POS2"],
      ["address", "Slave address"], ["ident", "Manufacturer + type"], ["key_id", "Key ID"],
      ["angle", "Slat angle"], ["status_bits", "Status bits"],
    ],
    diag: [
      ["status", "Status flags"], ["key_id_compare", "Compare key ID"],
      ["write_address", "Write slave address"], ["identify", "Identify (wink)"],
    ],
  };

  const app = {
    state: null,
    meta: null,
    selected: null,
    lines: new Map(),
    traffic: [],
    lastSeq: 0,
    driveSig: "",
    view: "front",
    detail: null,
  };

  // --- helpers -------------------------------------------------------------------

  async function api(method, path, body) {
    const res = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (_) { data = { error: text }; }
    if (!res.ok) throw new Error((data && data.error) || res.statusText);
    return data;
  }

  let toastTimer = null;
  function toast(msg, bad) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.toggle("bad", !!bad);
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (t.hidden = true), 3500);
  }

  async function run(fn) {
    try { return await fn(); } catch (e) { toast(e.message, true); }
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function findMotor(uid) {
    if (!app.state) return null;
    for (const bus of app.state.buses) {
      for (const m of bus.motors) if (m.uid === uid) return { bus, motor: m };
    }
    return null;
  }

  function fmtTime(t) {
    const d = new Date(t * 1000);
    return d.toTimeString().slice(0, 8) + "." + String(d.getMilliseconds()).padStart(3, "0");
  }

  // --- theme ---------------------------------------------------------------------

  function initTheme() {
    let saved = null;
    try { saved = localStorage.getItem("smisim-theme"); } catch (_) { /* ignore */ }
    if (saved) document.documentElement.dataset.theme = saved;
    $("#theme").addEventListener("click", () => {
      const cur = document.documentElement.dataset.theme
        || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      const next = cur === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("smisim-theme", next); } catch (_) { /* ignore */ }
    });
  }

  // --- tabs ----------------------------------------------------------------------

  function initTabs() {
    $$(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  }

  function showTab(name) {
    $$(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    $$(".tab-body").forEach((p) => (p.hidden = p.id !== `tab-${name}`));
  }

  // --- lines / facade ------------------------------------------------------------

  function renderLines() {
    const root = $("#lines");
    const buses = app.state.buses;
    for (const [idx, line] of app.lines) {
      if (idx >= buses.length) {
        line.article.remove();
        app.lines.delete(idx);
      }
    }
    buses.forEach((bus) => {
      let line = app.lines.get(bus.index);
      if (!line) {
        const article = document.createElement("article");
        article.className = "line";
        article.innerHTML = `
          <header class="line-head">
            <div class="line-title">
              <h2></h2>
              <span class="badge variant"></span>
            </div>
            <div class="line-stats"></div>
          </header>
          <div class="endpoints"></div>
          <div class="dup-warning" hidden></div>
          <div class="facade-wrap"></div>
          <div class="line-buttons">
            <button data-act="all-up">▲ All up</button>
            <button data-act="all-stop">■ Stop all</button>
            <button data-act="all-down">▼ All down</button>
            <button data-act="add" class="ghost">+ Drive</button>
          </div>`;
        root.appendChild(article);
        const facade = new window.Facade.Line($(".facade-wrap", article), selectDrive);
        line = { article, facade };
        app.lines.set(bus.index, line);
        article.addEventListener("click", (e) => {
          const act = e.target.closest("button") && e.target.closest("button").dataset.act;
          if (!act) return;
          if (act === "add") return run(() => api("POST", `/api/buses/${bus.index}/motors`, {}));
          const code = { "all-up": "up", "all-stop": "stop", "all-down": "down" }[act];
          run(() => api("POST", `/api/buses/${bus.index}/send`, { mode: "broadcast", type: "command", code }));
        });
      }
      const a = line.article;
      $("h2", a).textContent = `${bus.index + 1}. ${bus.settings.name}`;
      $(".variant", a).textContent = bus.settings.variant;
      const util = Math.round(bus.utilization * 100);
      $(".line-stats", a).innerHTML = `
        <span title="Drives on this line">${bus.motors.length}/16 drives</span>
        <span title="Telegrams received">${bus.stats.telegrams} tg</span>
        <span title="Telegrams without answer">${bus.stats.no_answer} silent</span>
        <span title="Framing / checksum errors" class="${bus.stats.errors ? "warn" : ""}">${bus.stats.errors} err</span>
        <span class="util" title="Bus utilisation over 10 s"><i style="width:${util}%"></i><b>${util}%</b></span>
        ${bus.discovery ? '<span class="pill pill-wait">discovery…</span>' : ""}`;
      $(".endpoints", a).innerHTML = bus.endpoints.length
        ? bus.endpoints.map((e) => `<code class="chip" title="${esc(e.kind)}">${esc(e.kind)}: ${esc(e.endpoint)}${e.clients ? ` · ${e.clients} client(s)` : ""}${e.error ? ` · ${esc(e.error)}` : ""}</code>`).join("")
        : '<span class="muted small">No external endpoint. Use the built-in master.</span>';
      const dup = $(".dup-warning", a);
      if (bus.duplicates.length) {
        dup.hidden = false;
        dup.textContent = `Address conflict: ${bus.duplicates.join(", ")} used by more than one drive. Movement commands still work, but reads collide. Commission the drives (Master tab → Discover).`;
      } else dup.hidden = true;
      line.facade.update(bus, app.selected, app.view);
    });
    // bus selectors
    const opts = buses.map((b) => `<option value="${b.index}">${b.index + 1}. ${esc(b.settings.name)}</option>`).join("");
    const mBus = $("#m-bus");
    if (mBus.dataset.sig !== opts) {
      const keep = mBus.value;
      mBus.innerHTML = opts;
      mBus.dataset.sig = opts;
      if (keep && Number(keep) < buses.length) mBus.value = keep;
      const monLine = $("#mon-line");
      const keepMon = monLine.value;
      monLine.innerHTML = '<option value="">All lines</option>' + opts;
      monLine.value = keepMon;
    }
  }

  function animate() {
    let last = performance.now();
    function frame(now) {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      for (const line of app.lines.values()) line.facade.frame(dt);
      if (app.detail) app.detail.frame(dt);
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  // --- drive panel ---------------------------------------------------------------

  function selectDrive(uid) {
    app.selected = uid;
    app.driveSig = "";
    showTab("drive");
    renderDrive();
    renderLines();
  }

  function renderDrive() {
    const found = app.selected != null ? findMotor(app.selected) : null;
    $("#drive-empty").hidden = !!found;
    const box = $("#drive");
    box.hidden = !found;
    if (!found) { app.detail = null; return; }
    const { bus, motor: m } = found;
    // Rebuild the static part only when identity/config changes (keeps form focus).
    const sig = JSON.stringify([m.uid, m.kind, m.config, Object.keys(app.meta.faults)]);
    if (sig !== app.driveSig) {
      app.driveSig = sig;
      box.innerHTML = driveTemplate(bus, m);
      bindDrive(box, bus, m);
      app.detail = new window.Facade.Detail($(".d-views", box), m);
    }
    app.detail.update(m);
    $(".d-live", box).innerHTML = liveTemplate(m);
    $$("input[data-fault]", box).forEach((cb) => (cb.checked = m.faults.includes(cb.dataset.fault)));
  }

  function driveTemplate(bus, m) {
    const faults = Object.entries(app.meta.faults).map(([k, v]) => `
      <label class="fault" title="${esc(v)}"><input type="checkbox" data-fault="${k}"> <span>${k.replace(/_/g, " ")}</span><small>${esc(v)}</small></label>`).join("");
    const c = m.config;
    const kinds = app.meta.kinds.map((k) => `<option ${k === m.kind ? "selected" : ""}>${k}</option>`).join("");
    return `
      <div class="d-head">
        <h3>${esc(m.name || "Drive " + m.uid)}</h3>
        <span class="muted">Line ${bus.index + 1} · ${m.kind}</span>
      </div>
      <dl class="ident">
        <div><dt>Slave address</dt><dd>${m.address}</dd></div>
        <div><dt>Manufacturer</dt><dd>${m.manufacturer}</dd></div>
        <div><dt>Key ID</dt><dd><code>${m.key_id}</code></dd></div>
        <div><dt>Type</dt><dd>${m.drive_type}</dd></div>
      </dl>
      <div class="d-views" aria-hidden="false"></div>
      <p class="muted small views-caption">Left: front view (lift). Right: side section, outside on the left, room on the right${m.kind === "venetian" ? " (slat angle)" : ""}.</p>
      <div class="d-live"></div>
      <h4>Control <span class="muted small">(sent as SMI telegrams)</span></h4>
      <div class="ctl-grid">
        <button data-cmd="up">▲ Up</button>
        <button data-cmd="stop">■ Stop</button>
        <button data-cmd="down">▼ Down</button>
        <button data-cmd="pos1">Pos 1</button>
        <button data-cmd="pos2">Pos 2</button>
        <button data-cmd="read">Read pos</button>
      </div>
      <label class="grow">Lift: go to <output class="goto-out">${Math.round(m.percent)} %</output>
        <input type="range" class="goto" min="0" max="100" step="1" value="${Math.round(m.percent)}">
      </label>
      ${m.kind === "venetian" ? `
      <label class="check"><input type="checkbox" class="keep-slat" checked> Keep the slat angle after the move (sends GOTO with position + angle)</label>
      <label class="grow">Slats: <output class="angle-out">${Math.round(m.slat_percent)} %</output>
        <input type="range" class="angle" min="0" max="100" step="1" value="${Math.round(m.slat_percent)}">
      </label>
      <div class="ctl-grid">
        <button data-slat="0" title="Slat position 0 % (open)">Open slats</button>
        <button data-slat="50" title="Slat position 50 %">Half</button>
        <button data-slat="100" title="Slat position 100 % (closed)">Close slats</button>
        <button data-step="up" title="UP with 10 degrees of shaft rotation">Step ▲ 10°</button>
        <button data-step="down" title="DOWN with 10 degrees of shaft rotation">Step ▼ 10°</button>
      </div>` : ""}
      <h4>Commissioning</h4>
      <div class="ctl-grid">
        <button data-act="calibrate" title="Teach the end positions: run up, down and up again">Calibrate</button>
        <button data-act="wink" title="Make the drive visible in the facade">Identify</button>
        <button data-act="clear" title="Clear faults and resettable errors">Clear faults</button>
      </div>
      <h4>Fault injection</h4>
      <div class="faults">${faults}</div>
      <h4>Configuration</h4>
      <form class="cfg stack">
        <div class="row">
          <label class="grow">Name <input name="name" value="${esc(m.name)}" maxlength="40"></label>
          <label>Kind <select name="kind">${kinds}</select></label>
        </div>
        <div class="row">
          <label>Address <input type="number" name="address" min="0" max="15" value="${m.address}"></label>
          <label>Manufacturer <input type="number" name="manufacturer" min="0" max="15" value="${m.manufacturer}"></label>
          <label>Key ID <input name="key_id" value="${m.key_id}" pattern="[0-9A-Fa-f]{1,8}"></label>
        </div>
        <div class="row">
          <label>Travel time (s) <input type="number" name="travel_time_s" min="2" max="600" step="0.5" value="${c.travel_time_s}"></label>
          <label>Tilt range (shaft °) <input type="number" name="tilt_degrees" min="30" max="720" value="${c.tilt_degrees}"></label>
        </div>
        <div class="row">
          <label>Slat angle at 0 % (°) <input type="number" name="slat_min_deg" min="-90" max="90" value="${c.slat_min_deg}"></label>
          <label>Slat angle at 100 % (°) <input type="number" name="slat_max_deg" min="-90" max="90" value="${c.slat_max_deg}"></label>
          <label>Reversal pause (s) <input type="number" name="reversal_pause_s" min="0" max="5" step="0.05" value="${c.reversal_pause_s}"></label>
        </div>
        ${m.kind === "venetian" ? `<label class="check" title="Provisional: see the Protocol tab"><input type="checkbox" name="tilt_in_position" ${c.tilt_in_position ? "checked" : ""}> Slat turning counts in the position (shaft rotation)</label>` : ""}
        <div class="row">
          <label>Pos 1 (%) <input type="number" name="pos1" min="0" max="100" step="0.1" value="${(c.pos1 / 655.35).toFixed(1)}"></label>
          <label>Pos 2 (%) <input type="number" name="pos2" min="0" max="100" step="0.1" value="${(c.pos2 / 655.35).toFixed(1)}"></label>
        </div>
        <div class="row">
          <button type="submit" class="primary">Save</button>
          <button type="button" data-act="remove" class="danger ghost">Remove drive</button>
        </div>
      </form>`;
  }

  function liveTemplate(m) {
    const dir = m.reversing ? "reversing…" : m.direction < 0 ? "▲ moving up" : m.direction > 0 ? "▼ moving down" : "stopped";
    const errs = m.errors.map((e) => `<span class="chip bad">${esc(e)}</span>`).join("");
    const faults = m.faults.map((e) => `<span class="chip warn">${esc(e)}</span>`).join("");
    return `
      <div class="gauges">
        <div class="gauge"><span>Position</span><b>${m.percent.toFixed(1)} %</b><small title="Value the drive reports over SMI">SMI ${m.reported_position}</small></div>
        ${m.kind === "venetian" ? `<div class="gauge"><span>Slats</span><b>${Math.round(m.slat_percent)} %</b><small>${m.slat_angle > 0 ? "+" : ""}${Math.round(m.slat_angle)}° · shaft ${Math.round(m.angle_deg)}°</small></div>` : ""}
        <div class="gauge"><span>Daylight in room</span><b>${Math.round(m.daylight * 100)} %</b><i class="bar day"><i style="width:${m.daylight * 100}%"></i></i></div>
        <div class="gauge"><span>State</span><b class="${m.direction ? "accent" : ""}">${dir}</b><small>${m.calibrating ? "calibrating" : m.limits_set ? "limits set" : "limits NOT set"}</small></div>
        <div class="gauge"><span>Motor heat</span><b>${Math.round(m.heat * 100)} %</b><i class="bar"><i style="width:${m.heat * 100}%"></i></i></div>
      </div>
      <div class="chips">${errs}${faults}</div>
      <p class="muted small">Telegrams ${m.stats.telegrams} · commands ${m.stats.commands} · reads ${m.stats.queries} · runs ${m.stats.runs} · run time ${m.stats.run_time_s.toFixed(0)} s</p>
      ${m.stats.last_command ? `<p class="small">Last: <code>${esc(m.stats.last_command)}</code></p>` : ""}`;
  }

  function bindDrive(box, bus, m) {
    const send = (req) => run(async () => {
      const out = await api("POST", `/api/buses/${bus.index}/send`, Object.assign({ mode: "slave", address: currentAddress() }, req));
      if (out.response && !["ack", "data", "flags"].includes(out.response.kind)) toast(`Drive answered: ${out.response.text}`, true);
      else if (out.response && out.response.kind === "data") toast(`${out.sent} → ${out.reply}  =  ${out.response.text}`);
    });
    const currentAddress = () => (findMotor(m.uid) ? findMotor(m.uid).motor.address : m.address);
    $$("[data-cmd]", box).forEach((b) => b.addEventListener("click", () => {
      const c = b.dataset.cmd;
      if (c === "read") return send({ type: "query", code: "position" });
      send({ type: "command", code: c });
    }));
    $$("[data-step]", box).forEach((b) => b.addEventListener("click", () =>
      send({ type: "command", code: b.dataset.step, angle_deg: 10 })));
    // SMI angle data is motor-shaft degrees in 2 degree units; slat % maps onto the tilt range.
    const shaftDeg = (pct) => {
      const live = findMotor(m.uid);
      const range = live ? live.motor.config.tilt_degrees : m.config.tilt_degrees;
      return Math.min(510, Math.round((pct / 100) * range / 2) * 2);
    };
    const go = $(".goto", box);
    go.addEventListener("input", () => ($(".goto-out", box).textContent = `${go.value} %`));
    go.addEventListener("change", () => {
      const req = { type: "command", code: "goto", position: Math.round(go.value * 655.35) };
      const keep = $(".keep-slat", box);
      const live = findMotor(m.uid);
      if (keep && keep.checked && live) req.angle_deg = shaftDeg(live.motor.slat_percent);
      send(req);
    });
    const ang = $(".angle", box);
    if (ang) {
      ang.addEventListener("input", () => ($(".angle-out", box).textContent = `${ang.value} %`));
      ang.addEventListener("change", () => send({ type: "command", code: "goto", angle_deg: shaftDeg(Number(ang.value)) }));
    }
    $$("[data-slat]", box).forEach((b) => b.addEventListener("click", () =>
      send({ type: "command", code: "goto", angle_deg: shaftDeg(Number(b.dataset.slat)) })));
    $$("[data-act]", box).forEach((b) => b.addEventListener("click", () => {
      const act = b.dataset.act;
      if (act === "remove" && !confirm("Remove this drive from the line?")) return;
      run(async () => {
        await api("POST", `/api/motors/${m.uid}/${act}`, {});
        if (act === "remove") { app.selected = null; renderDrive(); }
      });
    }));
    $$("input[data-fault]", box).forEach((cb) => cb.addEventListener("change", () =>
      run(() => api("POST", `/api/motors/${m.uid}/fault`, { name: cb.dataset.fault, active: cb.checked }))));
    $(".cfg", box).addEventListener("submit", (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      const body = {
        name: f.get("name"), kind: f.get("kind"),
        address: Number(f.get("address")), manufacturer: Number(f.get("manufacturer")),
        key_id: String(f.get("key_id")),
        travel_time_s: Number(f.get("travel_time_s")), tilt_degrees: Number(f.get("tilt_degrees")),
        slat_min_deg: Number(f.get("slat_min_deg")), slat_max_deg: Number(f.get("slat_max_deg")),
        reversal_pause_s: Number(f.get("reversal_pause_s")),
        tilt_in_position: f.get("tilt_in_position") === "on",
        pos1: Math.round(Number(f.get("pos1")) * 655.35), pos2: Math.round(Number(f.get("pos2")) * 655.35),
      };
      run(async () => { await api("PATCH", `/api/motors/${m.uid}`, body); app.driveSig = ""; toast("Saved"); });
    });
  }

  // --- master panel --------------------------------------------------------------

  function initMaster() {
    const mask = $("#m-mask");
    for (let i = 0; i < 16; i++) {
      mask.insertAdjacentHTML("beforeend", `<label><input type="checkbox" value="${i}"><span>${i}</span></label>`);
    }
    const form = $("#master-form");
    const fillCodes = () => {
      const t = $("#m-type").value;
      $("#m-code").innerHTML = CODES[t].map(([v, l]) => `<option value="${v}">${l}</option>`).join("");
    };
    fillCodes();
    $("#m-type").addEventListener("change", () => { fillCodes(); syncMaster(); });
    form.addEventListener("input", syncMaster);
    form.addEventListener("change", syncMaster);
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const req = masterRequest();
      run(async () => {
        const out = await api("POST", `/api/buses/${$("#m-bus").value || 0}/send`, req);
        showResult(out);
      });
    });
    $("#raw-form").addEventListener("submit", (e) => {
      e.preventDefault();
      const hex = new FormData(e.target).get("hex");
      run(async () => showResult(await api("POST", `/api/buses/${$("#m-bus").value || 0}/send`, { hex })));
    });
    $("#discover").addEventListener("click", () => run(async () => {
      await api("POST", `/api/buses/${$("#m-bus").value || 0}/discover`, { manufacturer: 0 });
      toast("Discovery started. Watch the bus monitor.");
    }));
    syncMaster();
  }

  function masterRequest() {
    const f = new FormData($("#master-form"));
    const mode = f.get("mode");
    const type = f.get("type");
    const code = f.get("code");
    const req = { mode, type, code, manufacturer: Number(f.get("manufacturer") || 0) };
    if (mode === "slave") req.address = Number(f.get("address"));
    if (mode === "group") req.addresses = $$("#m-mask input:checked").map((c) => Number(c.value));
    if (mode === "key_id") req.key_id = String(f.get("key_id") || "0");
    const fields = masterFields(type, code);
    if (fields.includes("position")) req.position = Math.round(Number(f.get("position_pct")) * 655.35);
    if (fields.includes("angle") && f.get("angle_deg") !== "") req.angle_deg = Number(f.get("angle_deg"));
    if (fields.includes("search")) req.search = String(f.get("search") || "0");
    if (fields.includes("new_address")) req.new_address = Number(f.get("new_address"));
    if (type === "command" && (code === "pos1" || code === "pos2") && !$("#m-store").checked) delete req.position;
    return req;
  }

  function masterFields(type, code) {
    if (type === "command") {
      if (code === "goto") return ["position", "angle"];
      if (code === "pos1" || code === "pos2") return ["position", "angle", "store"];
      if (code === "up" || code === "down") return ["angle"];
      return [];
    }
    if (type === "diag") {
      if (code === "key_id_compare") return ["search"];
      if (code === "write_address") return ["new_address"];
    }
    return [];
  }

  let previewTimer = null;
  function syncMaster() {
    const f = new FormData($("#master-form"));
    const mode = f.get("mode");
    $$("#master-form [data-show]").forEach((row) => (row.hidden = !row.dataset.show.split(" ").includes(mode)));
    const fields = masterFields(f.get("type"), f.get("code"));
    $$("#master-form [data-field]").forEach((row) => (row.hidden = !fields.includes(row.dataset.field)));
    let store = $("#m-store");
    if (!store) {
      $("#master-form [data-field=position]").insertAdjacentHTML("afterend",
        '<label class="check" data-field="store"><input type="checkbox" id="m-store"> Store this position (instead of recalling it)</label>');
      store = $("#m-store");
    }
    $("#master-form [data-field=store]").hidden = !fields.includes("store");
    $("#m-pos-out").textContent = `${f.get("position_pct")} %`;
    clearTimeout(previewTimer);
    previewTimer = setTimeout(async () => {
      try {
        const out = await api("POST", "/api/encode", masterRequest());
        $("#m-preview").innerHTML = `${esc(out.hex)} <span class="badge ${out.telegram.status}">${out.telegram.status}</span><br><span class="muted">${esc(out.telegram.text)}</span>`;
      } catch (e) {
        $("#m-preview").textContent = e.message;
      }
    }, 120);
  }

  function showResult(out) {
    const r = $("#m-result");
    r.hidden = false;
    const resp = out.response;
    const cls = resp ? (["ack", "data", "flags"].includes(resp.kind) ? "ok" : "bad") : "bad";
    r.innerHTML = `
      <div><span class="muted">Sent</span> <code>${esc(out.sent)}</code></div>
      <div><span class="muted">Answer</span> <code>${esc(out.reply || "(none)")}</code></div>
      <div class="${cls}">${resp ? esc(resp.text) : esc((out.decoded && out.decoded.error) || "not a valid master telegram")}</div>`;
  }

  // --- test lab ------------------------------------------------------------------

  function initLab() {
    $("#lab-start").addEventListener("click", () =>
      run(() => api("POST", "/api/testlab/start", { include_ui: $("#lab-ui").checked })));
    $("#lab-stop").addEventListener("click", () => run(() => api("POST", "/api/testlab/stop")));
    $("#lab-checks").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-arm]");
      if (b) run(async () => toast(`Armed on ${(await api("POST", `/api/testlab/arm/${b.dataset.arm}`)).armed}`));
    });
  }

  function renderLab() {
    const lab = app.state.testlab;
    $("#lab-start").textContent = lab.active ? "Restart session" : "Start session";
    const icons = { pending: "○", running: "◔", pass: "✔", fail: "✘", info: "i" };
    $("#lab-checks").innerHTML = lab.checks.map((c) => `
      <li class="check-item ${c.status}">
        <span class="ico" aria-label="${c.status}">${icons[c.status] || "?"}</span>
        <div>
          <b>${esc(c.title)}</b>
          <p class="muted small">${esc(c.description)}</p>
          ${c.detail ? `<p class="small">${esc(c.detail)}</p>` : ""}
        </div>
        ${c.armable ? `<button data-arm="${c.id}" ${lab.active ? "" : "disabled"}>Arm</button>` : ""}
      </li>`).join("");
  }

  // --- protocol table ------------------------------------------------------------

  function renderSpec() {
    $("#spec-rows").innerHTML = Object.entries(app.meta.spec_status).map(([k, v]) => `
      <tr><td><code>${esc(k)}</code></td><td><span class="badge ${v.status}">${v.status}</span></td><td>${esc(v.source)}</td></tr>`).join("");
  }

  // --- monitor -------------------------------------------------------------------

  const DIR = { M: "→", S: "←", E: "!", I: "i" };

  function addTraffic(entries) {
    if (!entries.length) return;
    app.traffic.push(...entries);
    if (app.traffic.length > 5000) app.traffic.splice(0, app.traffic.length - 5000);
    app.lastSeq = entries[entries.length - 1].seq;
    if (!$("#mon-pause").checked) renderTraffic();
  }

  function renderTraffic() {
    const text = $("#mon-filter").value.trim().toLowerCase();
    const line = $("#mon-line").value;
    const errOnly = $("#mon-errors").checked;
    const rows = [];
    for (let i = app.traffic.length - 1; i >= 0 && rows.length < 400; i--) {
      const e = app.traffic[i];
      if (line !== "" && String(e.bus) !== line) continue;
      if (errOnly && e.ok) continue;
      if (text && !(`${e.hex} ${e.text} ${e.source}`.toLowerCase().includes(text))) continue;
      rows.push(e);
    }
    $("#mon-rows").innerHTML = rows.map((e) => `
      <tr class="dir-${e.direction} ${e.ok ? "" : "not-ok"}">
        <td class="t">${fmtTime(e.t)}</td>
        <td>${e.bus + 1}</td>
        <td class="d" title="${{ M: "master telegram", S: "drive answer", E: "error", I: "info" }[e.direction]}">${DIR[e.direction] || ""}</td>
        <td>${esc(e.source)}</td>
        <td><code>${esc(e.hex)}</code></td>
        <td>${esc(e.text)}${e.status === "provisional" ? ' <span class="badge provisional">provisional</span>' : ""}</td>
      </tr>`).join("");
  }

  function initMonitor() {
    ["#mon-filter", "#mon-line", "#mon-errors", "#mon-pause"].forEach((s) =>
      $(s).addEventListener("input", renderTraffic));
    $("#mon-clear").addEventListener("click", () => { app.traffic = []; renderTraffic(); });
    $("#mon-export").addEventListener("click", () => {
      // The server builds the CSV (raw position, rail % and slat % in their own columns).
      // Start after the entries cleared from this view.
      const since = app.traffic.length ? app.traffic[0].seq - 1 : app.lastSeq;
      const a = document.createElement("a");
      a.href = `/api/traffic.csv?since=${since}`;
      a.download = "smi-bus-trace.csv";
      a.click();
    });
  }

  // --- top bar -------------------------------------------------------------------

  function initTop() {
    const preset = $("#preset");
    for (const [k, label] of Object.entries(app.meta.presets)) {
      preset.insertAdjacentHTML("beforeend", `<option value="${k}">${esc(label)}</option>`);
    }
    preset.addEventListener("change", () => {
      const v = preset.value;
      preset.value = "";
      if (v) run(async () => { await api("POST", `/api/presets/${v}`); app.selected = null; toast("Preset loaded"); });
    });
    const view = $("#view-mode");
    try { app.view = localStorage.getItem("smisim-view") || "front"; } catch (_) { /* ignore */ }
    view.value = app.view;
    view.addEventListener("change", () => {
      app.view = view.value;
      try { localStorage.setItem("smisim-view", app.view); } catch (_) { /* ignore */ }
      if (app.state) renderLines();
    });
    $("#time-scale").addEventListener("change", (e) =>
      run(() => api("POST", "/api/settings", { time_scale: Number(e.target.value) })));
    $("#add-line").addEventListener("click", () => run(() => api("POST", "/api/buses", { motors: 16 })));
    $("#remove-line").addEventListener("click", () => run(() => api("DELETE", "/api/buses/last")));
  }

  // --- live connection -----------------------------------------------------------

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws?since=${app.lastSeq}`);
    const pill = $("#conn");
    ws.onopen = () => { pill.textContent = "live"; pill.className = "pill pill-ok"; };
    ws.onclose = () => {
      pill.textContent = "reconnecting";
      pill.className = "pill pill-wait";
      setTimeout(connect, 1500);
    };
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type !== "state") return;
      app.state = msg.state;
      const ts = $("#time-scale");
      if (document.activeElement !== ts) {
        const v = String(app.state.time_scale);
        if (![...ts.options].some((o) => o.value === v)) ts.insertAdjacentHTML("beforeend", `<option value="${v}">${v}×</option>`);
        ts.value = v;
      }
      renderLines();
      renderDrive();
      renderLab();
      addTraffic(msg.traffic);
      const drives = app.state.buses.reduce((n, b) => n + b.motors.length, 0);
      $("#footer-stats").textContent = `${app.state.buses.length} line(s) · ${drives} drives`;
    };
  }

  async function main() {
    initTheme();
    initTabs();
    app.meta = await api("GET", "/api/meta");
    initTop();
    initMaster();
    initLab();
    initMonitor();
    renderSpec();
    animate();
    connect();
  }

  main().catch((e) => toast(e.message, true));
})();
