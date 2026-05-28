const gridEl = document.querySelector("#warehouse-grid");
const robotsEl = document.querySelector("#robots-list");
const ordersEl = document.querySelector("#orders-list");
const chargingEl = document.querySelector("#charging-list");
const eventLogEl = document.querySelector("#event-log");
const connectionEl = document.querySelector("#connection-state");

const metricOrders = document.querySelector("#metric-orders");
const metricCompleted = document.querySelector("#metric-completed");
const metricExpired = document.querySelector("#metric-expired");
const metricFailed = document.querySelector("#metric-failed");
const metricAvgTask = document.querySelector("#metric-avg-task");
const metricBusyRobots = document.querySelector("#metric-busy-robots");
const metricUptime = document.querySelector("#metric-uptime");

const cellName = (x, y) => `${String.fromCharCode(65 + x)}${y + 1}`;
const pointKey = (point) => `${point.x}:${point.y}`;

let gridMeta = null;
let routeLayer = null;
const robotTokens = new Map();

function setConnection(label, kind) {
  connectionEl.textContent = label;
  connectionEl.className = `connection-state ${kind}`;
}

function robotKind(robot) {
  const mode = String(robot.mode || "idle").toLowerCase();
  if (mode.includes("charging")) return "charging";
  if (mode.includes("charger")) return "to-charger";
  if (mode.includes("stuck")) return "stuck";
  if (mode.includes("pickup") || mode.includes("deliver")) return "working";
  if (Number(robot.battery) < 40) return "low";
  return "idle";
}

function renderState(state) {
  setConnection("Live", "online");
  metricOrders.textContent = state.orders.total;
  metricCompleted.textContent = state.orders.completed;
  metricExpired.textContent = state.orders.expired;
  metricFailed.textContent = state.orders.failed;
  metricAvgTask.textContent = `${state.orders.avgCompletionSeconds}s`;
  metricBusyRobots.textContent = `${state.robotStats.busy}/${state.robotStats.total}`;
  metricUptime.textContent = `${state.uptimeSeconds}s`;
  renderGrid(state);
  renderRobots(state.robots);
  renderOrders(state.orders.active);
  renderCharging(state.charging);
  renderEvents(state.events);
}

function renderGrid(state) {
  ensureGrid(state.world);

  const pickups = new Set(state.orders.active.map((order) => pointKey(order.pickup)));
  const dropoffs = new Set(state.orders.active.map((order) => pointKey(order.dropoff)));

  for (const [key, cell] of gridMeta.cells) {
    cell.classList.toggle("pickup", pickups.has(key));
    cell.classList.toggle("active-dropoff", dropoffs.has(key));
  }

  renderRoutes(state.orders.active, state.robots);
  renderRobotOverlay(state);
}

function ensureGrid(world) {
  const nextKey = `${world.width}:${world.height}`;
  if (gridMeta && gridMeta.key === nextKey) return;

  gridEl.style.gridTemplateColumns = `repeat(${world.width}, minmax(26px, 1fr))`;
  const shelves = new Set(world.shelves.map(pointKey));
  const chargers = new Set(world.chargingStations.map(pointKey));
  const packaging = pointKey(world.packagingZone);
  const cells = new Map();
  const elements = [];

  for (let y = 0; y < world.height; y += 1) {
    for (let x = 0; x < world.width; x += 1) {
      const key = `${x}:${y}`;
      const cell = document.createElement("div");
      cell.className = "cell";
      if (shelves.has(key)) cell.classList.add("shelf");
      if (chargers.has(key)) cell.classList.add("charger");
      if (packaging === key) cell.classList.add("packaging");

      const label = document.createElement("span");
      label.className = "cell-label";
      label.textContent = cellName(x, y);
      cell.append(label);

      const asset = document.createElement("span");
      asset.className = "asset-mark";
      if (packaging === key) asset.textContent = "P";
      else if (chargers.has(key)) asset.textContent = "C";
      else if (shelves.has(key)) asset.textContent = "S";
      if (asset.textContent) cell.append(asset);

      cells.set(key, cell);
      elements.push(cell);
    }
  }

  routeLayer = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  routeLayer.classList.add("route-layer");
  routeLayer.setAttribute("aria-hidden", "true");

  gridEl.replaceChildren(...elements, routeLayer);
  robotTokens.clear();
  gridMeta = { key: nextKey, cells, width: world.width, height: world.height };
}

function renderRoutes(orders, robots) {
  if (!routeLayer) return;
  const rect = gridEl.getBoundingClientRect();
  routeLayer.setAttribute("viewBox", `0 0 ${rect.width} ${rect.height}`);

  routeLayer.replaceChildren(
    ...orders.flatMap((order) => {
      const start = cellCenter(order.pickup);
      const end = cellCenter(order.dropoff);
      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.classList.add("route-path", order.status);
      line.setAttribute("x1", String(start.x));
      line.setAttribute("y1", String(start.y));
      line.setAttribute("x2", String(end.x));
      line.setAttribute("y2", String(end.y));

      const marker = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      marker.classList.add("route-marker", order.status);
      marker.setAttribute("cx", String(start.x));
      marker.setAttribute("cy", String(start.y));
      marker.setAttribute("r", "5");

      return [line, marker];
    }),
    ...robots.flatMap((robot) => {
      if (!Array.isArray(robot.path) || robot.path.length < 1) return [];
      const points = [robot.position, ...robot.path].map(cellCenter);
      const polyline = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
      polyline.classList.add("robot-path", robotKind(robot));
      polyline.setAttribute(
        "points",
        points.map((point) => `${point.x},${point.y}`).join(" ")
      );
      return [polyline];
    })
  );
}

function renderRobotOverlay(state) {
  const activeIds = new Set(state.robots.map((robot) => robot.id));
  const grouped = new Map();

  for (const robot of state.robots) {
    const key = pointKey(robot.position);
    const group = grouped.get(key) || [];
    group.push(robot);
    grouped.set(key, group);
  }

  for (const robot of state.robots) {
    const token = ensureRobotToken(robot);
    const group = grouped.get(pointKey(robot.position)) || [robot];
    const index = group.findIndex((item) => item.id === robot.id);
    const offset = robotOffset(index, group.length);
    const position = cellCenter(robot.position);

    token.className = `robot-token ${robotKind(robot)}`;
    token.textContent = robot.id;
    token.title = `${robot.id} ${robot.mode} ${robot.battery}%`;
    token.style.left = `${position.x}px`;
    token.style.top = `${position.y}px`;
    token.style.setProperty("--offset-x", `${offset.x}px`);
    token.style.setProperty("--offset-y", `${offset.y}px`);
  }

  for (const [robotId, token] of robotTokens) {
    if (!activeIds.has(robotId)) {
      token.remove();
      robotTokens.delete(robotId);
    }
  }
}

function ensureRobotToken(robot) {
  const existing = robotTokens.get(robot.id);
  if (existing) return existing;

  const token = document.createElement("span");
  token.className = `robot-token ${robotKind(robot)}`;
  token.textContent = robot.id;
  const position = cellCenter(robot.position);
  token.style.left = `${position.x}px`;
  token.style.top = `${position.y}px`;
  gridEl.append(token);
  robotTokens.set(robot.id, token);
  return token;
}

function cellCenter(point) {
  const rect = gridEl.getBoundingClientRect();
  const styles = getComputedStyle(gridEl);
  const gap = Number.parseFloat(styles.columnGap || styles.gap || "0") || 0;
  const cellWidth = (rect.width - gap * (gridMeta.width - 1)) / gridMeta.width;
  const cellHeight = (rect.height - gap * (gridMeta.height - 1)) / gridMeta.height;
  return {
    x: point.x * (cellWidth + gap) + cellWidth / 2,
    y: point.y * (cellHeight + gap) + cellHeight / 2,
  };
}

function robotOffset(index, total) {
  if (total <= 1) return { x: 0, y: 0 };
  const radius = total === 2 ? 11 : 14;
  const angle = (Math.PI * 2 * index) / total - Math.PI / 2;
  return {
    x: Math.round(Math.cos(angle) * radius),
    y: Math.round(Math.sin(angle) * radius),
  };
}

function renderRobots(robots) {
  if (!robots.length) {
    robotsEl.replaceChildren(emptyRow("Waiting for robot status"));
    return;
  }

  robotsEl.replaceChildren(
    ...robots.map((robot) => {
      const kind = robotKind(robot);
      const card = document.createElement("article");
      card.className = "robot-card";

      const head = document.createElement("div");
      head.className = "robot-head";
      const id = document.createElement("span");
      id.className = "robot-id";
      id.textContent = robot.id;
      const mode = document.createElement("span");
      mode.className = `mode-pill ${kind}`;
      mode.textContent = robot.mode;
      head.append(id, mode);

      const battery = document.createElement("div");
      battery.className = "battery";
      const level = document.createElement("div");
      level.className = "battery-level";
      const value = Math.max(0, Math.min(100, Number(robot.battery)));
      level.style.width = `${value}%`;
      if (value < 25) level.classList.add("low");
      else if (value < 55) level.classList.add("mid");
      battery.append(level);

      const details = document.createElement("div");
      details.className = "detail-line";
      details.textContent = robot.stuckReason
        ? `${robot.position.label}, battery ${robot.battery}%, ${robot.stuckReason}`
        : `${robot.position.label}, battery ${robot.battery}%`;
      card.append(head, battery, details);
      return card;
    })
  );
}

function renderOrders(orders) {
  if (!orders.length) {
    ordersEl.replaceChildren(emptyRow("No active orders"));
    return;
  }

  ordersEl.replaceChildren(
    ...orders.map((order) => {
      const row = document.createElement("article");
      row.className = "order-row";

      const head = document.createElement("div");
      head.className = "order-head";
      const id = document.createElement("span");
      id.className = "order-id";
      id.textContent = order.id;
      const status = document.createElement("span");
      status.className = `order-status ${order.status}`;
      status.textContent = order.status;
      head.append(id, status);

      const route = document.createElement("div");
      route.className = "detail-line";
      route.textContent = `${order.pickup.label} -> ${order.dropoff.label}`;

      const owner = document.createElement("div");
      owner.className = "detail-line";
      owner.textContent = `Assigned: ${order.assignedRobot || "auction"}`;

      row.append(head, route, owner);
      if (Array.isArray(order.bids) && order.bids.length) {
        const bids = document.createElement("div");
        bids.className = "bid-line";
        bids.textContent = order.bids
          .map((bid) => `${bid.robotId}: ${bid.etaSeconds}s`)
          .join(" | ");
        row.append(bids);
      }
      return row;
    })
  );
}

function renderCharging(charging) {
  const rows = [];
  for (const item of charging.occupied) {
    const row = document.createElement("div");
    row.className = "charge-row";
    row.textContent = `${item.robotId} at ${item.station.label}`;
    rows.push(row);
  }
  for (const robotId of charging.waiting) {
    const row = document.createElement("div");
    row.className = "charge-row";
    row.textContent = `${robotId} waiting`;
    rows.push(row);
  }
  chargingEl.replaceChildren(...(rows.length ? rows : [emptyRow("All stations free")]));
}

function renderEvents(events) {
  if (!events.length) {
    eventLogEl.replaceChildren(emptyRow("Waiting for messages"));
    return;
  }

  eventLogEl.replaceChildren(
    ...events
      .slice()
      .reverse()
      .map((event) => {
        const row = document.createElement("article");
        row.className = "event-row";
        const topic = document.createElement("span");
        topic.className = "event-topic";
        topic.textContent = `${event.sender} -> ${event.recipient} / ${event.topic}`;
        const text = document.createElement("div");
        text.className = "event-text";
        text.textContent = event.text;
        row.append(topic, text);
        return row;
      })
  );
}

function emptyRow(text) {
  const row = document.createElement("div");
  row.className = "empty-row";
  row.textContent = text;
  return row;
}

async function poll() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderState(await response.json());
  } catch (error) {
    setConnection("Offline", "offline");
  }
}

window.addEventListener("resize", () => {
  for (const token of robotTokens.values()) {
    token.style.transition = "none";
  }
  poll().finally(() => {
    requestAnimationFrame(() => {
      for (const token of robotTokens.values()) {
        token.style.transition = "";
      }
    });
  });
});

poll();
setInterval(poll, 250);
