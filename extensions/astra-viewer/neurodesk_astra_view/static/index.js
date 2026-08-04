/**
 * SVG renderer for the ASTRA provenance projection.
 *
 * The Python model ships two graphs: the semantic graph (one node per
 * declared record — the inspector's source of truth) and a presentation
 * projection (stages, grouped inputs/outputs, decision clusters, a synthetic
 * result node) built by projection.py. This file draws the projection and
 * never re-derives it: grouping and layout arithmetic live in Python where
 * they are testable, and every node arrives with a rank/order pair per view.
 *
 * The renderer rebuilds the SVG from scratch on every mode change, cluster
 * expansion, and selection, so each filter re-lays out what it actually
 * draws — rows are compacted over the visible nodes, never inherited from a
 * fuller picture.
 */

const RANK_GAP = 110;
const COLUMN_GAP = 240;
const MARGIN = 62;
const MIN_HEIGHT = 420;

const COLORS = {
  stage: "#3b7f8c",
  input: "#3563a8",
  "input-group": "#3563a8",
  output: "#2e7d5b",
  "output-group": "#2e7d5b",
  "decision-cluster": "#b07818",
  decision: "#b07818",
  finding: "#7a4fae",
  "finding-group": "#7a4fae",
  insight: "#8c72a8",
  result: "#26241f",
};

const EDGE_STYLE = {
  flow: { color: "#9b978e", width: 1.45 },
  produces: { color: "#9b978e", width: 1.45 },
  configures: { color: "#b07818", dash: "3 4", width: 1.35 },
  supports: { color: "#7a4fae", dash: "6 3", width: 1.45 },
  informs: { color: "#8c72a8", dash: "1 3", width: 1.3 },
  concludes: { color: "#7a4fae", width: 1.8 },
};

const STATUS_RING = [
  [/verif/, "#2f855a"],
  [/fail|mismatch/, "#c53030"],
  [/executed|run/, "#b7791f"],
];

/** Base node kinds drawn by the dataflow-centred views. */
const BASE_KINDS = [
  "stage",
  "input",
  "input-group",
  "output",
  "output-group",
  "finding",
  "finding-group",
  "result",
];

/** Edge kinds drawn by the dataflow-centred views. */
const BASE_EDGE_KINDS = ["flow", "produces", "supports", "concludes"];

/** Claim-carrying kinds; the Evidence mode is built around these. */
const CLAIM_KINDS = ["insight", "finding", "finding-group"];

function appendText(parent, tag, text, className) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = text == null ? "" : String(text);
  parent.appendChild(element);
  return element;
}

function svgEl(name, attrs = {}) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attrs)) {
    element.setAttribute(key, String(value));
  }
  return element;
}

function shortLabel(label, max = 38) {
  const text = label == null ? "" : String(label);
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}

function statusRing(status) {
  const value = status == null ? "" : String(status);
  const entry = STATUS_RING.find(([pattern]) => pattern.test(value));
  return entry ? entry[1] : null;
}

// ---------------------------------------------------------------------------
// Mode filters
//
// A mode that shows everything is indistinguishable from the one before it,
// so each mode is a strict filter over the projection: `flow` draws the
// dataflow skeleton, `decisions` adds the decision clusters, `evidence`
// swaps to the claims subgraph the evidence_rank layout covers.

function visibleProjection(projection, mode, expanded) {
  const nodes = projection.nodes.filter((node) => {
    if (mode === "evidence") return node.evidence_rank != null;
    if (node.kind === "decision-cluster") return mode === "decisions";
    if (node.kind === "decision") {
      return mode === "decisions" && expanded.has(node.parent);
    }
    if (node.kind === "insight") return false;
    return node.rank != null;
  });
  const visible = new Set(nodes.map((node) => node.id));
  const edges = projection.edges.filter((edge) => {
    if (!visible.has(edge.source) || !visible.has(edge.target)) return false;
    if (mode === "evidence") {
      return ["informs", "configures", "supports", "concludes"].includes(
        edge.kind,
      );
    }
    if (edge.kind === "configures") {
      if (edge.parent) return expanded.has(edge.parent);
      // The cluster's own edge to its stage disappears once it is expanded:
      // the member edges say more.
      return !expanded.has(edge.source);
    }
    return BASE_EDGE_KINDS.includes(edge.kind);
  });
  return { nodes, edges };
}

// ---------------------------------------------------------------------------
// Geometry
//
// Positions come from the rank/order pair layout.py computed for the active
// view; this function only turns the pair into coordinates, compacting rows
// over what is drawn. Decision clusters sit beside their stage and expand
// into a box of rows — presentation relative to a ranked node, not layout.

function layoutVisible(visible, mode, expanded) {
  const rankKey = mode === "evidence" ? "evidence_rank" : "rank";
  const orderKey = mode === "evidence" ? "evidence_order" : "order";
  const ranked = visible.nodes.filter((node) => node[rankKey] != null);
  const rows = new Map();
  ranked.forEach((node) => {
    const rank = node[rankKey];
    if (!rows.has(rank)) rows.set(rank, []);
    rows.get(rank).push(node);
  });
  const leftGutter = mode === "decisions" ? 300 : 60;
  // A crowded rank breaks into staggered sub-rows: side labels need ~230px
  // each, so past a handful per row the labels collide long before the dots
  // do. Stacking order-adjacent nodes into short columns keeps every label
  // readable, and the canvas widens to fit the widest rank instead of
  // clamping nodes into a pile at the edges — the viewport scrolls anyway.
  const SUBROW_GAP = 34;
  const MAX_PER_SUBROW = 6;
  const plans = [...rows.keys()]
    .sort((left, right) => left - right)
    .map((rank) => {
      const members = rows
        .get(rank)
        .sort((left, right) => left[orderKey] - right[orderKey]);
      const subrows = Math.ceil(members.length / MAX_PER_SUBROW);
      return { members, subrows, columns: Math.ceil(members.length / subrows) };
    });
  const widestColumns = Math.max(1, ...plans.map((plan) => plan.columns));
  const width = Math.max(720, leftGutter + (widestColumns - 1) * COLUMN_GAP + 340);
  const positions = new Map();
  let bandY = MARGIN;
  plans.forEach(({ members, subrows, columns }) => {
    const gap =
      columns > 1
        ? Math.min(COLUMN_GAP, (width - leftGutter - 340) / (columns - 1))
        : COLUMN_GAP;
    const span = (columns - 1) * gap;
    const start = leftGutter + (width - leftGutter - span) / 2;
    const labelMax = subrows > 1 ? 28 : 38;
    members.forEach((node, index) => {
      const column = Math.floor(index / subrows);
      const subrow = index % subrows;
      positions.set(node.id, {
        node,
        labelMax,
        x: Math.max(46, start + column * gap + subrow * 16),
        y: bandY + subrow * SUBROW_GAP,
      });
    });
    bandY += RANK_GAP + (subrows - 1) * SUBROW_GAP;
  });

  const clusterBoxes = [];
  visible.nodes
    .filter((node) => node.kind === "decision-cluster")
    .forEach((cluster) => {
      const target = positions.get(cluster.target);
      const anchorX = target ? target.x : leftGutter;
      const anchorY = target ? target.y : MARGIN;
      const members = visible.nodes.filter(
        (node) => node.parent === cluster.id,
      );
      if (!expanded.has(cluster.id)) {
        positions.set(cluster.id, {
          node: cluster,
          x: Math.max(38, anchorX - 190),
          y: anchorY,
        });
        return;
      }
      const rowHeight = 27;
      const boxHeight = 42 + members.length * rowHeight;
      const boxY = Math.max(18, anchorY - boxHeight / 2);
      const boxX = Math.max(20, anchorX - 470);
      clusterBoxes.push({ x: boxX, y: boxY, width: 280, height: boxHeight });
      positions.set(cluster.id, { node: cluster, x: boxX + 16, y: boxY + 20 });
      members.forEach((member, index) => {
        positions.set(member.id, {
          node: member,
          x: boxX + 24,
          y: boxY + 49 + index * rowHeight,
        });
      });
    });

  const height = Math.max(
    MIN_HEIGHT,
    ...[...positions.values()].map((entry) => entry.y + 92),
    ...clusterBoxes.map((box) => box.y + box.height + 30),
  );
  return { width, height, positions, clusterBoxes };
}

function edgePath(source, target) {
  const radius = source.node.kind.startsWith("decision") ? 9 : 13;
  const targetRadius = target.node.kind.startsWith("decision") ? 9 : 13;
  if (Math.abs(source.y - target.y) < 35) {
    const direction = source.x <= target.x ? 1 : -1;
    return `M ${source.x + direction * radius} ${source.y} L ${
      target.x - direction * targetRadius
    } ${target.y}`;
  }
  const downward = target.y >= source.y;
  const y1 = source.y + (downward ? radius : -radius);
  const y2 = target.y - (downward ? targetRadius : -targetRadius);
  const mid = y1 + (y2 - y1) * 0.52;
  return `M ${source.x} ${y1} C ${source.x} ${mid}, ${target.x} ${mid}, ${target.x} ${y2}`;
}

// ---------------------------------------------------------------------------
// Inspector
//
// The projection node names what is drawn; its `members` name the semantic
// records behind it, and those carry the run facts, recipes, artifacts, and
// previews. A single-record node inspects that record in full; a group lists
// its members.

function renderPreview(parent, preview) {
  if (!preview) return;
  const section = appendText(parent, "section", "", "astra-preview");
  appendText(section, "h4", "Result preview");
  if (preview.kind === "figure") {
    const image = document.createElement("img");
    image.src = preview.data_url;
    image.alt = preview.path || "ASTRA result figure";
    section.appendChild(image);
  } else if (preview.kind === "metric") {
    appendText(
      section,
      "div",
      `${
        typeof preview.value === "object"
          ? JSON.stringify(preview.value)
          : preview.value
      }${preview.units ? ` ${preview.units}` : ""}`,
      "astra-metric",
    );
  } else if (preview.kind === "table") {
    const table = document.createElement("table");
    const head = document.createElement("tr");
    (preview.columns || []).forEach((value) => appendText(head, "th", value));
    table.appendChild(head);
    (preview.rows || []).forEach((row) => {
      const tr = document.createElement("tr");
      row.forEach((value) => appendText(tr, "td", value));
      table.appendChild(tr);
    });
    section.appendChild(table);
  } else if (preview.kind === "report") {
    appendText(section, "pre", preview.text, "astra-report");
  }
  if (preview.path) {
    appendText(
      section,
      "div",
      `${preview.path} · ${preview.size} bytes`,
      "astra-path",
    );
  }
}

function renderSemanticDetails(panel, node) {
  if (!node) return;
  if (node.recipe) {
    appendText(panel, "h4", "Resolved recipe");
    appendText(panel, "pre", node.recipe);
  }
  if ((node.selected_decisions || []).length) {
    appendText(panel, "h4", "Selected decisions");
    const list = document.createElement("ul");
    node.selected_decisions.forEach((item) => {
      appendText(
        list,
        "li",
        `${item.decision_id}: ${item.value == null ? "unresolved" : item.value}`,
      );
    });
    panel.appendChild(list);
  }
  if (node.when) {
    appendText(panel, "h4", "Condition");
    appendText(panel, "div", `${JSON.stringify(node.when)} → ${node.when_satisfied}`);
  }
  if (node.resources) {
    appendText(panel, "h4", "Resources");
    appendText(panel, "pre", JSON.stringify(node.resources, null, 2));
  }
  if (node.run && Object.keys(node.run).length) {
    appendText(panel, "h4", "Run facts");
    appendText(panel, "pre", JSON.stringify(node.run, null, 2));
  }
  if (node.artifact) {
    appendText(panel, "h4", "Artifact");
    appendText(
      panel,
      "div",
      `${node.artifact.path} · ${node.artifact.sha256 || "hash unavailable"}`,
      "astra-path",
    );
    renderPreview(panel, node.artifact.preview);
  }
  if ((node.warnings || []).length) {
    appendText(panel, "h4", "Warnings");
    const list = document.createElement("ul");
    node.warnings.forEach((warning) => appendText(list, "li", warning));
    panel.appendChild(list);
  }
}

function renderInspector(panel, view, context) {
  panel.replaceChildren();
  if (!view) {
    appendText(panel, "h3", "Hover or select a node");
    appendText(
      panel,
      "p",
      "Its provenance role, selected option, run facts, and result preview appear here.",
      "astra-muted",
    );
    return panel;
  }
  const head = appendText(panel, "div", "", "astra-inspector-head");
  const chip = appendText(head, "span", view.kind.replace("-group", ""), "astra-kind");
  chip.style.background = COLORS[view.kind] || "#5c584f";
  appendText(head, "span", view.label, "astra-inspector-title");
  if (view.description) appendText(panel, "p", view.description);
  (view.meta || []).forEach((line) => appendText(panel, "div", line, "astra-meta"));

  if ((view.options || []).length) {
    const list = appendText(panel, "div", "", "astra-options");
    view.options.forEach((option) => {
      const row = appendText(
        list,
        "div",
        "",
        `astra-option${option.selected ? " selected" : ""}`,
      );
      appendText(row, "span", option.selected ? "●" : "○");
      appendText(row, "span", option.label);
      if (option.description) row.title = option.description;
    });
  }

  const informing = context.projection.edges
    .filter((edge) => edge.kind === "informs" && edge.target === view.id)
    .map((edge) => context.viewById.get(edge.source))
    .filter(Boolean);
  if (informing.length) {
    const box = appendText(panel, "div", "", "astra-informed");
    appendText(box, "div", "Informed by", "astra-informed-title");
    informing.forEach((insight) => {
      appendText(
        box,
        "div",
        insight.description ? `${insight.label}: ${insight.description}` : insight.label,
      );
    });
  }

  const members = (view.members || [])
    .map((id) => context.nodeById.get(id))
    .filter((node) => node && node.kind !== "evidence");
  if (members.length === 1) {
    renderSemanticDetails(panel, members[0]);
  } else if (members.length > 1) {
    appendText(panel, "h4", `${members.length} records`);
    const list = document.createElement("ul");
    members.forEach((member) => {
      appendText(
        list,
        "li",
        `${member.label}${
          member.status && member.status !== "unknown" ? ` · ${member.status}` : ""
        }`,
      );
    });
    panel.appendChild(list);
  }
  return panel;
}

// ---------------------------------------------------------------------------
// Legend

function buildLegend(state, refresh) {
  const panel = document.createElement("aside");
  panel.className = "astra-overlay astra-legend";
  const toggle = appendText(panel, "button", "", "astra-legend-toggle");
  toggle.type = "button";
  toggle.setAttribute("aria-expanded", String(state.legendOpen));
  appendText(toggle, "span", "Graph grammar", "astra-legend-title");
  appendText(
    toggle,
    "span",
    state.legendOpen ? "▾ collapse" : "▸ expand",
    "astra-legend-chevron",
  );
  toggle.addEventListener("click", () => {
    state.legendOpen = !state.legendOpen;
    refresh();
  });
  if (!state.legendOpen) return panel;
  const body = appendText(panel, "div", "", "astra-legend-body");
  [
    ["#9b978e", "data / artifact flow", false],
    ["#b07818", "decision configures", true],
    ["#7a4fae", "supports a finding", true],
    ["#8c72a8", "insight informs", true],
    ["#7a4fae", "concludes in result", false],
  ].forEach(([color, label, dashed]) => {
    const row = appendText(body, "div", "", "astra-legend-row");
    const line = appendText(row, "span", "", `astra-legend-line${dashed ? " dashed" : ""}`);
    line.style.setProperty("--edge", color);
    appendText(row, "span", label);
  });
  return panel;
}

// ---------------------------------------------------------------------------
// Widget entry point

export function render({ model, el }) {
  const graph = model.get("graph");
  const root = document.createElement("div");
  root.className = "astra-viewer";
  el.appendChild(root);

  const header = document.createElement("header");
  header.className = "astra-header";
  root.appendChild(header);
  const title = appendText(header, "div", "", "astra-title");
  appendText(title, "strong", graph.meta.analysis_name || "ASTRA analysis");
  if (graph.meta.universe_id) {
    appendText(title, "span", `universe: ${graph.meta.universe_id}`, "astra-universe");
  }

  if ((graph.warnings || []).length) {
    const warnings = document.createElement("div");
    warnings.className = "astra-warnings";
    // A spec that predates several schema changes warns once per analysis per
    // change, which is a wall of yellow above the graph the reader came for.
    // Past a handful they collapse behind a count that still names the cause.
    const host =
      graph.warnings.length > 3 ? appendText(warnings, "details", "") : warnings;
    if (host !== warnings) {
      appendText(host, "summary", `${graph.warnings.length} schema warnings`);
    }
    graph.warnings.forEach((warning) => appendText(host, "div", warning));
    root.appendChild(warnings);
  }

  if ((graph.errors || []).length) {
    const errors = document.createElement("div");
    errors.className = "astra-errors";
    appendText(errors, "h3", "ASTRA validation failed");
    graph.errors.forEach((error) => appendText(errors, "div", error));
    root.appendChild(errors);
    return;
  }

  // Trust is a claim about a graph that got drawn, so it is appended only
  // past the failure return: "Nothing here was executed" reads as a
  // description of the picture, and on the invalid path there is no picture.
  const provenance = appendText(header, "div", "", "astra-provenance");
  const badge = appendText(
    provenance,
    "span",
    graph.trust.label,
    `astra-trust astra-trust-${graph.trust.level}`,
  );
  badge.title = graph.trust.message;
  appendText(provenance, "span", graph.trust.message, "astra-trust-message");

  const toolbar = document.createElement("nav");
  toolbar.className = "astra-toolbar";
  root.appendChild(toolbar);
  ["flow", "decisions", "evidence"].forEach((mode) => {
    const button = appendText(toolbar, "button", mode[0].toUpperCase() + mode.slice(1));
    button.type = "button";
    button.dataset.mode = mode;
    button.addEventListener("click", () => {
      model.set("mode", mode);
      model.save_changes();
    });
  });
  const collapseAll = appendText(toolbar, "button", "Collapse decisions");
  collapseAll.type = "button";
  collapseAll.className = "astra-collapse-all";
  collapseAll.addEventListener("click", () => {
    model.set("expanded", []);
    model.save_changes();
  });

  // A mode that filters to nothing must say so; silently redrawing the
  // previous picture is what made the Evidence button look inert.
  const notice = appendText(
    root,
    "div",
    "This analysis declares no findings, prior insights, or evidence, so there "
      + "is nothing for the Evidence view to show. Add prior_insights or "
      + "findings to the spec to populate it.",
    "astra-notice",
  );
  notice.hidden = true;

  // The canvas is the visible frame: the viewport scrolls both axes inside
  // it, while the legend and inspector overlays are its direct children so
  // they stay pinned to the visible corners instead of scrolling away with
  // the drawing.
  const canvas = document.createElement("div");
  canvas.className = "astra-canvas";
  root.appendChild(canvas);

  const projection = graph.projection || { nodes: [], edges: [] };
  const context = {
    projection,
    nodeById: new Map(graph.nodes.map((node) => [node.id, node])),
    viewById: new Map(projection.nodes.map((node) => [node.id, node])),
  };
  const state = { legendOpen: false, hovered: null };
  const hasClaims = projection.nodes.some((node) => CLAIM_KINDS.includes(node.kind));

  const inspectorFor = (id) =>
    renderInspector(
      document.createElement("aside"),
      id ? context.viewById.get(id) : null,
      context,
    );

  const updateInspector = () => {
    const panel = viewport.querySelector(".astra-inspector");
    if (!panel) return;
    const shown = state.hovered || model.get("selected_node");
    const replacement = inspectorFor(shown);
    replacement.className = "astra-overlay astra-inspector";
    panel.replaceWith(replacement);
  };

  const activateNode = (node) => {
    state.refocus = node.id;
    if (node.kind === "decision-cluster") {
      const expanded = new Set(model.get("expanded") || []);
      expanded.has(node.id) ? expanded.delete(node.id) : expanded.add(node.id);
      model.set("expanded", [...expanded]);
    }
    model.set("selected_node", node.id);
    model.save_changes();
    draw();
  };

  const drawNode = (svg, entry, selected, expanded) => {
    const { node, x, y, labelMax } = entry;
    const group = svgEl("g", {
      class: `astra-node${node.kind === "decision-cluster" ? " is-cluster" : ""}${
        selected === node.id ? " is-selected" : ""
      }`,
      transform: `translate(${x} ${y})`,
      tabindex: 0,
      role: node.kind === "decision-cluster" ? "button" : "img",
      "aria-label": node.label,
    });
    const color = COLORS[node.kind] || "#5c584f";
    const isDecision = node.kind === "decision" || node.kind === "decision-cluster";
    const radius = node.kind === "stage" || node.kind === "result" ? 7.5 : 6;
    group.appendChild(
      svgEl("circle", {
        class: "astra-focus",
        r: radius + 5,
        fill: "none",
        stroke: color,
        "stroke-width": 2,
        opacity: 0,
      }),
    );
    if (isDecision) {
      const size = node.kind === "decision" ? 5 : 6;
      group.appendChild(
        svgEl("rect", {
          x: -size,
          y: -size,
          width: size * 2,
          height: size * 2,
          fill: node.kind === "decision" ? "#fffdfa" : color,
          stroke: color,
          "stroke-width": node.kind === "decision" ? 2 : 0,
          transform: "rotate(45)",
        }),
      );
    } else {
      const ring = statusRing(node.status);
      if (ring) {
        group.appendChild(
          svgEl("circle", {
            r: radius + 3.5,
            fill: "none",
            stroke: ring,
            "stroke-width": 1.6,
          }),
        );
      }
      group.appendChild(svgEl("circle", { r: radius, fill: color }));
    }
    if (node.kind === "stage") {
      const overline = svgEl("text", { class: "astra-stage-label", x: 11, y: -10 });
      overline.textContent = "ANALYSIS STAGE";
      group.appendChild(overline);
    }
    const text = svgEl("text", {
      class: isDecision ? "astra-label astra-decision-label" : "astra-label",
      x: 11,
      y: 4,
    });
    const suffix =
      node.kind === "decision-cluster"
        ? expanded.has(node.id)
          ? " ▾"
          : " ▸"
        : "";
    text.textContent = shortLabel(node.label, labelMax || 38) + suffix;
    group.appendChild(text);
    group.addEventListener("mouseenter", () => {
      state.hovered = node.id;
      updateInspector();
    });
    group.addEventListener("mouseleave", () => {
      state.hovered = null;
      updateInspector();
    });
    group.addEventListener("click", () => activateNode(node));
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        activateNode(node);
      }
    });
    if (state.refocus === node.id) state.focusTarget = group;
    svg.appendChild(group);
  };

  const draw = () => {
    const mode = model.get("mode");
    const expanded = new Set(model.get("expanded") || []);
    const selected = model.get("selected_node");
    toolbar.querySelectorAll("button[data-mode]").forEach((button) => {
      button.classList.toggle("active", button.dataset.mode === mode);
    });
    collapseAll.hidden = expanded.size === 0 || mode !== "decisions";
    notice.hidden = !(mode === "evidence" && !hasClaims);

    const visible = visibleProjection(projection, mode, expanded);
    const layout = layoutVisible(visible, mode, expanded);
    // Every redraw rebuilds the viewport, which would reset its scroll
    // position to the origin — a click far down the graph must not fling
    // the reader back to the top.
    const previousViewport = canvas.querySelector(".astra-viewport");
    const scrollLeft = previousViewport ? previousViewport.scrollLeft : 0;
    const scrollTop = previousViewport ? previousViewport.scrollTop : 0;
    state.focusTarget = null;
    canvas.replaceChildren();
    const viewport = document.createElement("div");
    viewport.className = "astra-viewport";

    const svg = svgEl("svg", {
      class: "astra-svg",
      width: layout.width,
      height: layout.height,
      viewBox: `0 0 ${layout.width} ${layout.height}`,
      role: "img",
      "aria-label": `${graph.meta.analysis_name || "ASTRA"} provenance graph`,
    });
    const defs = svgEl("defs");
    for (const [kind, style] of Object.entries(EDGE_STYLE)) {
      const marker = svgEl("marker", {
        id: `astra-arrow-${kind}`,
        markerWidth: 7,
        markerHeight: 7,
        refX: 6,
        refY: 3.5,
        orient: "auto",
      });
      marker.appendChild(svgEl("path", { d: "M0,0 L7,3.5 L0,7 z", fill: style.color }));
      defs.appendChild(marker);
    }
    svg.appendChild(defs);

    layout.clusterBoxes.forEach((box) => {
      svg.appendChild(
        svgEl("rect", {
          class: "astra-cluster-box",
          x: box.x,
          y: box.y,
          width: box.width,
          height: box.height,
          rx: 8,
        }),
      );
    });

    visible.edges.forEach((edge) => {
      const source = layout.positions.get(edge.source);
      const target = layout.positions.get(edge.target);
      if (!source || !target) return;
      const style = EDGE_STYLE[edge.kind] || EDGE_STYLE.flow;
      const path = svgEl("path", {
        class: "astra-edge",
        d: edgePath(source, target),
        stroke: style.color,
        "stroke-width": style.width,
        "marker-end": `url(#astra-arrow-${edge.kind})`,
        opacity:
          selected && edge.source !== selected && edge.target !== selected
            ? 0.22
            : 0.72,
      });
      if (style.dash) path.setAttribute("stroke-dasharray", style.dash);
      svg.appendChild(path);
    });

    layout.positions.forEach((entry) => drawNode(svg, entry, selected, expanded));
    viewport.appendChild(svg);
    canvas.appendChild(viewport);
    viewport.scrollLeft = scrollLeft;
    viewport.scrollTop = scrollTop;
    canvas.appendChild(buildLegend(state, draw));
    const inspector = inspectorFor(state.hovered || selected);
    inspector.className = "astra-overlay astra-inspector";
    canvas.appendChild(inspector);
    if (state.focusTarget) {
      // Re-focus the activated node in the rebuilt drawing without letting
      // the browser scroll it into view — the restored offsets are already
      // right, and a focus-scroll would jump the viewport again.
      state.focusTarget.focus?.({ preventScroll: true });
      state.focusTarget = null;
      state.refocus = null;
    }
  };

  const applyMode = () => draw();
  const applyExpanded = () => draw();
  const onSelectedNodeChange = () => draw();
  model.on("change:mode", applyMode);
  model.on("change:expanded", applyExpanded);
  model.on("change:selected_node", onSelectedNodeChange);
  draw();

  if ((graph.gaps || []).length) {
    const details = document.createElement("details");
    details.className = "astra-gaps";
    appendText(details, "summary", `Provenance gaps (${graph.gaps.length})`);
    graph.gaps.forEach((gap) =>
      appendText(details, "div", `${gap.id} · ${gap.severity} · ${gap.message}`),
    );
    root.appendChild(details);
  }

  return () => {
    model.off("change:mode", applyMode);
    model.off("change:expanded", applyExpanded);
    model.off("change:selected_node", onSelectedNodeChange);
    root.remove();
  };
}
