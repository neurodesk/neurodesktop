function appendText(parent, tag, text, className) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = text == null ? "" : String(text);
  parent.appendChild(element);
  return element;
}

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
      `${typeof preview.value === "object" ? JSON.stringify(preview.value) : preview.value}${preview.units ? ` ${preview.units}` : ""}`,
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
    appendText(section, "div", `${preview.path} · ${preview.size} bytes`, "astra-path");
  }
}

function renderInspector(panel, node) {
  panel.replaceChildren();
  if (!node) {
    appendText(panel, "p", "Select a node to inspect its ASTRA and run facts.", "astra-muted");
    return;
  }
  appendText(panel, "h3", node.label || node.id);
  appendText(panel, "div", node.kind, "astra-kind");
  if (node.description) appendText(panel, "p", node.description);
  if (node.recipe) {
    appendText(panel, "h4", "Resolved recipe");
    appendText(panel, "pre", node.recipe);
  }
  if ((node.selected_decisions || []).length) {
    appendText(panel, "h4", "Selected decisions");
    const list = document.createElement("ul");
    node.selected_decisions.forEach((item) => {
      appendText(list, "li", `${item.decision_id}: ${item.value == null ? "unresolved" : item.value}`);
    });
    panel.appendChild(list);
  }
  if ((node.options || []).length) {
    appendText(panel, "h4", "Options");
    const list = document.createElement("ul");
    node.options.forEach((item) => {
      appendText(list, "li", `${item.selected ? "✓ " : ""}${item.label}`);
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
    appendText(panel, "div", `${node.artifact.path} · ${node.artifact.sha256 || "hash unavailable"}`, "astra-path");
    renderPreview(panel, node.artifact.preview);
  }
  if ((node.warnings || []).length) {
    appendText(panel, "h4", "Warnings");
    const list = document.createElement("ul");
    node.warnings.forEach((warning) => appendText(list, "li", warning));
    panel.appendChild(list);
  }
}

/** Node kinds that carry a claim; the Evidence mode is built around these. */
const CLAIM_KINDS = ["insight", "finding", "evidence"];
/** Edges that attach a claim to what it rests on. */
const CLAIM_EDGE_KINDS = ["supports", "claims", "justifies"];
/** Classes that take an element out of the drawing, and so out of the layout. */
const HIDDEN_CLASSES = ["astra-hidden", "astra-collapsed", "astra-empty"];

const RANK_GAP = 132;
const COLUMN_GAP = 180;

const isHidden = (element) =>
  HIDDEN_CLASSES.some((name) => element.hasClass(name));

/**
 * A decision's selected value belongs to the decision, not to each of the
 * edges leaving it: repeating it per edge crowded the middle of the canvas
 * with labels that overlapped each other.
 */
function decisionLabel(node) {
  const chosen = (node.options || []).find((option) => option.selected);
  const value = chosen ? chosen.label : node.selected;
  return `${node.label}\n▸ ${value == null ? "unresolved" : value}`;
}

function cyElements(graph) {
  const nodes = graph.nodes.map((node) => ({
    data: {
      ...node,
      display_label:
        node.kind === "decision" ? decisionLabel(node) : node.label,
    },
  }));
  const edges = graph.edges.map((edge) => ({ data: { ...edge } }));
  return [...nodes, ...edges];
}

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
  // The badge names a run state, not a selection, and its explanation is the
  // whole point of it — so it reads next to the badge rather than in a
  // tooltip nobody discovers.
  const provenance = appendText(header, "div", "", "astra-provenance");
  const badge = appendText(provenance, "span", graph.trust.label, `astra-trust astra-trust-${graph.trust.level}`);
  badge.title = graph.trust.message;
  appendText(provenance, "span", graph.trust.message, "astra-trust-message");

  if ((graph.warnings || []).length) {
    const warnings = document.createElement("div");
    warnings.className = "astra-warnings";
    graph.warnings.forEach((warning) => appendText(warnings, "div", warning));
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

  const body = document.createElement("div");
  body.className = "astra-body";
  root.appendChild(body);
  const canvas = document.createElement("div");
  canvas.className = "astra-canvas";
  body.appendChild(canvas);
  const inspector = document.createElement("aside");
  inspector.className = "astra-inspector";
  body.appendChild(inspector);

  const cy = globalThis.cytoscape({
    container: canvas,
    elements: cyElements(graph),
    // Positions come from the rank and order the Python model computed; see
    // layoutVisible below and neurodesk_astra_view/layout.py for why.
    layout: { name: "preset" },
    style: [
      { selector: "node", style: { label: "data(display_label)", "font-size": 11, "text-wrap": "wrap", "text-max-width": 150, "background-color": "#4d6f8f", color: "#17202a", "text-valign": "bottom", "text-margin-y": 7, width: 38, height: 38 } },
      { selector: 'node[kind = "input"]', style: { shape: "barrel", "background-color": "#5b8db8" } },
      { selector: 'node[kind = "output"]', style: { shape: "round-rectangle", "background-color": "#77a86a" } },
      { selector: 'node[kind = "decision"]', style: { shape: "hexagon", "background-color": "#d2a84a" } },
      { selector: 'node[kind = "finding"], node[kind = "insight"], node[kind = "evidence"]', style: { shape: "round-tag", "background-color": "#a884bd" } },
      { selector: 'node[kind = "artifact"]', style: { shape: "document", "background-color": "#73a6a0" } },
      // The container title sits above its border: the base rule pushes
      // labels below a node, which drops a compound title onto the box edge.
      { selector: 'node[kind = "analysis"]', style: { shape: "round-rectangle", "background-opacity": 0.08, "border-width": 1, "border-color": "#718096", padding: 22, "text-valign": "top", "text-margin-y": -10, "text-max-width": 320, "font-size": 12, "font-weight": "bold", color: "#3d4854" } },
      { selector: "edge", style: { width: 2, "line-color": "#8996a3", "target-arrow-color": "#8996a3", "target-arrow-shape": "triangle", "curve-style": "bezier", label: "data(label)", "font-size": 9, "text-background-color": "#ffffff", "text-background-opacity": 0.85, "text-background-padding": 2 } },
      // The selected value now rides on the decision node itself.
      { selector: 'edge[kind = "parameterizes"]', style: { label: "", "line-style": "dashed", "line-color": "#bd8c26", "target-arrow-color": "#bd8c26" } },
      { selector: 'edge[kind = "supports"], edge[kind = "claims"], edge[kind = "justifies"]', style: { "line-style": "dotted", "line-color": "#9168a6", "target-arrow-color": "#9168a6" } },
      { selector: ".astra-hidden, .astra-collapsed, .astra-empty", style: { display: "none" } },
      { selector: ":selected", style: { "border-width": 4, "border-color": "#2b6cb0" } },
    ],
  });

  const nodeById = new Map(graph.nodes.map((node) => [node.id, node]));

  /**
   * Place the visible nodes on the semantic grid the Python model computed.
   *
   * Ranks are stable across modes, but a mode that hides a whole rank must
   * not leave a band of empty canvas behind, so rows and columns are
   * compacted over what is actually drawn. Re-running this on every filter
   * change is what the old single breadthfirst pass never did.
   */
  const layoutVisible = () => {
    const placed = cy
      .nodes()
      .filter((node) => node.data("rank") != null && !isHidden(node));
    if (placed.length === 0) return;
    const rows = new Map();
    placed.forEach((node) => {
      const rank = node.data("rank");
      if (!rows.has(rank)) rows.set(rank, []);
      rows.get(rank).push(node);
    });
    const positions = new Map();
    [...rows.keys()]
      .sort((left, right) => left - right)
      .forEach((rank, row) => {
        const members = rows
          .get(rank)
          .sort((left, right) => left.data("order") - right.data("order"));
        members.forEach((node, column) => {
          positions.set(node.id(), {
            x: (column - (members.length - 1) / 2) * COLUMN_GAP,
            y: row * RANK_GAP,
          });
        });
      });
    cy.layout({
      name: "preset",
      positions: (node) => positions.get(node.id()),
      fit: true,
      padding: 28,
      animate: false,
    }).run();
  };

  const applyCollapsed = () => {
    const collapsed = new Set(model.get("collapsed") || []);
    cy.elements().removeClass("astra-collapsed");
    collapsed.forEach((analysisId) => {
      cy.getElementById(analysisId).descendants().addClass("astra-collapsed");
    });
    // An analysis whose every member is filtered out would otherwise draw as
    // an empty labelled box. A collapsed one keeps its box: that is the point.
    cy.nodes('[kind = "analysis"]').removeClass("astra-empty");
    cy.nodes('[kind = "analysis"]').forEach((container) => {
      if (collapsed.has(container.id())) return;
      const alive = container
        .descendants()
        .filter((node) => !isHidden(node));
      if (alive.length === 0) container.addClass("astra-empty");
    });
    layoutVisible();
  };

  const applyMode = () => {
    const mode = model.get("mode");
    toolbar.querySelectorAll("button").forEach((button) => {
      button.classList.toggle("active", button.dataset.mode === mode);
    });
    cy.elements().removeClass("astra-hidden");
    const claims = cy
      .nodes()
      .filter((node) => CLAIM_KINDS.includes(node.data("kind")));
    if (mode === "flow") {
      cy.nodes().filter((node) => ["decision", ...CLAIM_KINDS].includes(node.data("kind"))).addClass("astra-hidden");
      cy.edges().filter((edge) => ["parameterizes", ...CLAIM_EDGE_KINDS].includes(edge.data("kind"))).addClass("astra-hidden");
    } else if (mode === "decisions") {
      cy.nodes().filter((node) => CLAIM_KINDS.includes(node.data("kind"))).addClass("astra-hidden");
      cy.edges().filter((edge) => CLAIM_EDGE_KINDS.includes(edge.data("kind"))).addClass("astra-hidden");
    } else if (mode === "evidence") {
      // Keep the claims and whatever they rest on; drop the dataflow
      // plumbing no claim depends on. Showing everything, which is what this
      // mode used to do, is not an evidence view.
      const kept = new Set(
        claims.union(claims.connectedEdges().connectedNodes()).map((node) => node.id()),
      );
      cy.nodes()
        .filter((node) => node.data("kind") !== "analysis" && !kept.has(node.id()))
        .addClass("astra-hidden");
      cy.edges()
        .filter((edge) => isHidden(edge.source()) || isHidden(edge.target()))
        .addClass("astra-hidden");
    }
    notice.hidden = !(mode === "evidence" && claims.length === 0);
    applyCollapsed();
  };

  const onTap = (event) => {
    const id = event.target.id();
    model.set("selected_node", id);
    model.save_changes();
    renderInspector(inspector, nodeById.get(id));
  };
  cy.on("tap", "node", onTap);
  const onCxttap = (event) => {
    const id = event.target.id();
    const collapsed = new Set(model.get("collapsed") || []);
    collapsed.has(id) ? collapsed.delete(id) : collapsed.add(id);
    model.set("collapsed", [...collapsed]);
    model.save_changes();
    applyCollapsed();
  };
  cy.on("cxttap", 'node[kind = "analysis"]', onCxttap);
  const onSelectedNodeChange = () => {
    const id = model.get("selected_node");
    cy.elements().unselect();
    if (id && nodeById.has(id)) cy.getElementById(id).select();
    renderInspector(inspector, nodeById.get(id));
  };
  model.on("change:mode", applyMode);
  model.on("change:collapsed", applyCollapsed);
  model.on("change:selected_node", onSelectedNodeChange);
  applyMode();
  renderInspector(inspector, nodeById.get(model.get("selected_node")));

  if ((graph.gaps || []).length) {
    const details = document.createElement("details");
    details.className = "astra-gaps";
    appendText(details, "summary", `Provenance gaps (${graph.gaps.length})`);
    graph.gaps.forEach((gap) => appendText(details, "div", `${gap.id} · ${gap.severity} · ${gap.message}`));
    root.appendChild(details);
  }

  return () => {
    model.off("change:mode", applyMode);
    model.off("change:collapsed", applyCollapsed);
    model.off("change:selected_node", onSelectedNodeChange);
    cy.destroy();
  };
}
