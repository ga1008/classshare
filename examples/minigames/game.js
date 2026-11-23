(async () => {
  "use strict";
  const sdk = window.LanGame, el = id => document.getElementById(id);
  const NS = "http://www.w3.org/2000/svg";
  let context = await sdk.ready(), { config, rules } = await sdk.definition(), pending = false, serial = 0;
  let chime;

  if (config.gameKey === "beacon-relay") {
    chime = new Audio();
    chime.preload = "none";
    chime.src = "static/返航 提示.wav";
    chime.muted = context.audio.muted;
    sdk.on("audio.changed", ({ muted }) => { chime.muted = muted; });
    window.addEventListener("pagehide", () => { chime.pause(); chime.removeAttribute("src"); }, { once: true });
  }

  const fail = e => {
    el("error").textContent = e.code === "ILLEGAL_MOVE" ? "方向有误，观察线索再试" : e.message;
  };
  document.title = el("title").textContent = config.title;
  el("icon").src = config.icon;
  el("player").textContent = context.environment === "local" ? "离线单机" : context.me.displayName;

  const navigation = {
    atPier: ["晨光从东方海面升起，点亮首座航标。", 0],
    atBay: ["沿地图向下航行，点亮南湾航标。", 1],
    atCape: ["夕阳正在海平面落下，向西推进。", 2],
    atChannel: ["船队已抵达航道，发出安全返航信号。", 3],
  };
  const stars = {
    first: "辨识星轨缺口：选出唯一的三角星芒",
    second: "推演星数序列：一 · 三 · 五 · ？",
    third: "嵌入核心四角星芒，合拢星图",
  };

  function svg(tag, attrs = {}) {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v));
    return n;
  }

  function renderBeaconScene(stepIndex) {
    const root = svg("svg", { viewBox: "0 0 320 96" });
    const pts = [[44, 58], [122, 34], [202, 64], [278, 38]];
    root.append(svg("path", {
      d: "M44 58 Q83 26 122 34 T202 64 T278 38",
      fill: "none",
      stroke: "currentColor",
      "stroke-opacity": "0.26",
      "stroke-width": "2.5",
      "stroke-dasharray": "5 5",
    }));
    pts.forEach(([x, y], idx) => {
      const lit = idx < stepIndex;
      const cur = idx === stepIndex;
      if (cur) {
        root.append(svg("circle", {
          cx: x, cy: y, r: 16,
          fill: "rgba(255, 206, 112, 0.2)",
          stroke: "#ffd482", "stroke-width": "1.5",
        }));
      }
      root.append(svg("circle", {
        cx: x, cy: y, r: lit ? 8 : cur ? 7 : 5.5,
        fill: lit ? "#ffd482" : cur ? "#5fe0c4" : "none",
        stroke: lit ? "#ffd482" : "currentColor",
        "stroke-opacity": lit || cur ? "1" : "0.4",
        "stroke-width": "2",
      }));
    });
    el("scene").replaceChildren(root);
  }

  function renderStarScene(node) {
    const progress = { first: 0, second: 1, third: 2, complete: 3 }[node] ?? 3;
    const root = svg("svg", { viewBox: "0 0 320 96" });
    const pts = [[64, 66], [134, 28], [198, 60], [262, 26]];
    for (let i = 0; i < pts.length - 1; i++) {
      const active = i < progress;
      root.append(svg("line", {
        x1: pts[i][0], y1: pts[i][1], x2: pts[i + 1][0], y2: pts[i + 1][1],
        stroke: active ? "#ffd482" : "currentColor",
        "stroke-opacity": active ? "0.9" : "0.2",
        "stroke-width": active ? "2.6" : "1.6",
      }));
    }
    pts.forEach(([x, y], idx) => {
      const done = idx <= progress;
      root.append(svg("circle", {
        cx: x, cy: y, r: done ? 7 : 4.5,
        fill: done ? "#ffd482" : "none",
        stroke: done ? "#ffd482" : "currentColor",
        "stroke-opacity": done ? "1" : "0.4",
        "stroke-width": "2",
      }));
    });
    el("scene").replaceChildren(root);
  }

  function renderTurnScene(turnSeat, blocked) {
    const bar = document.createElement("div");
    bar.className = "turn-bar";
    const dot = document.createElement("span");
    dot.className = "turn-dot turn-dot--" + turnSeat;
    const label = document.createElement("span");
    label.textContent = "第 " + turnSeat + " 席执子" + (blocked ? " · 等待对方" : " · 请落子");
    bar.append(dot, label);
    el("scene").replaceChildren(bar);
  }

  function button(label, action, disabled = false) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    b.disabled = disabled || pending;
    b.onclick = async () => {
      if (pending) return;
      pending = true;
      el("error").textContent = "";
      for (const control of el("board").querySelectorAll("button")) control.disabled = true;
      try {
        await action();
      } catch (e) {
        fail(e);
      } finally {
        pending = false;
        await render();
      }
    };
    return b;
  }

  async function render() {
    const generation = ++serial, snap = await sdk.session.snapshot();
    if (generation !== serial) return;
    const state = snap.state || {}, node = state.node, board = el("board");
    board.replaceChildren();
    board.className = "";
    if (snap.phase !== "running") {
      if (config.gameKey === "beacon-relay") renderBeaconScene(snap.outcome ? 4 : 0);
      else if (config.gameKey === "star-map") renderStarScene("complete");
      else el("scene").replaceChildren();
      el("prompt").textContent = snap.outcome
        ? state.winnerSeat
          ? "第 " + state.winnerSeat + " 席连线获胜"
          : snap.outcome === "draw"
            ? "棋盘已满，本局平局。"
            : "已完成本局。"
        : "等待开局…";
      return;
    }
    const blocked = context.environment === "platform" && snap.seat !== state.turnSeat;
    const move = async (type, payload) => {
      const result = await sdk.game.intent({ type, payload }, { expectedRevision: snap.revision, requestId: sdk.newRequestId() });
      if (chime && !chime.muted) { chime.currentTime = 0; chime.play().catch(() => {}); }
      return result;
    };
    if (rules.engine === "grid-line-v1") {
      board.className = "grid";
      renderTurnScene(state.turnSeat, blocked);
      el("prompt").textContent = "三子连线即胜";
      state.board.forEach((mark, i) => {
        const b = button("", () => move("place", { x: i % 3, y: Math.floor(i / 3) }), !!mark || blocked);
        const stone = document.createElement("span");
        stone.className = "stone stone--" + mark;
        stone.setAttribute("aria-hidden", "true");
        b.append(stone);
        b.setAttribute("aria-label", "第 " + (Math.floor(i / 3) + 1) + " 行第 " + (i % 3 + 1) + " 列" + (mark ? "，已占用" : ""));
        board.append(b);
      });
    } else if (config.gameKey === "beacon-relay") {
      const [clue, idx] = navigation[node];
      renderBeaconScene(idx);
      el("prompt").textContent = (blocked ? "队友回合 · " : "") + clue;
      if (node === "atChannel") {
        board.className = "dock-mode";
        board.append(button("引导船队返航", () => move("dock", { signal: "allClear" }), blocked));
      } else {
        board.className = "compass";
        const hub = document.createElement("span");
        hub.className = "compass-hub";
        hub.textContent = "✦";
        board.append(hub);
        for (const [bearing, label] of [["north", "北"], ["west", "西"], ["east", "东"], ["south", "南"]]) {
          const btn = button(label, () => move("light", { bearing }), blocked);
          btn.dataset.dir = bearing;
          board.append(btn);
        }
      }
    } else {
      board.className = "choices";
      renderStarScene(node);
      el("prompt").textContent = stars[node];
      const choices = {
        first: [["square", "□"], ["triangle", "△"], ["circle", "○"]],
        second: [["six", "六"], ["seven", "七"], ["eight", "八"]],
        third: [["spark", "✦"], ["dot", "●"], ["ring", "◎"]],
      };
      for (const [part, label] of choices[node]) board.append(button(label, () => move("fit", { part })));
    }
  }

  if (config.gameKey === "beacon-relay") {
    let saved = await sdk.progress.read();
    const appearance = () => document.body.classList.toggle("paper", saved.privateSave.mapStyle === "paper");
    appearance();
    el("style").hidden = false;
    el("style").onclick = async () => {
      try {
        saved = await sdk.progress.read();
        const values = { ...saved.privateSave, mapStyle: saved.privateSave.mapStyle === "paper" ? "night" : "paper" };
        await sdk.progress.writePrivate(values, { expectedRevision: saved.privateRevision, requestId: sdk.newRequestId() });
        saved = await sdk.progress.read();
        appearance();
      } catch (e) {
        fail(e);
      }
    };
  }

  if (context.environment === "local") {
    el("export").hidden = false;
    el("import-label").hidden = false;
    el("export").onclick = () => {
      const a = document.createElement("a");
      a.download = config.gameKey + "-save.json";
      const url = URL.createObjectURL(new Blob([JSON.stringify(sdk.local.export())], { type: "application/json" }));
      a.href = url;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 500);
    };
    el("import").onchange = async e => {
      try {
        const file = e.target.files[0];
        if (!file || file.size > 131072) throw Error("存档大小无效");
        sdk.local.import(JSON.parse(await file.text()));
        location.reload();
      } catch (err) {
        fail(err);
      }
    };
  }

  sdk.on("session.changed", v => { if (v.me) context = v; render().catch(fail); });
  for (const event of ["game.changed", "session.ended", "room.changed"]) sdk.on(event, () => render().catch(fail));
  await render();
})().catch(e => {
  document.getElementById("error").textContent = "游戏未能打开：" + e.message;
});
