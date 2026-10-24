const stageOrder = ["queued", "snapshotting", "comparing", "dependencies", "investigating", "validating", "completed"];
const stageLabels = {
  queued: "Queued",
  snapshotting: "Source snapshot",
  comparing: "Code comparison",
  dependencies: "Dependencies",
  investigating: "AI investigation",
  validating: "Recommendation",
  completed: "Complete",
  failed: "Failed",
};

function paintProgress(payload) {
  const current = payload.status || "";
  const currentIndex = stageOrder.indexOf(current);
  document.querySelectorAll("[data-stage]").forEach((item) => {
    const index = stageOrder.indexOf(item.dataset.stage);
    item.classList.toggle("done", current === "completed" || (currentIndex > index && index >= 0));
    item.classList.toggle("current", item.dataset.stage === current && current !== "completed" && current !== "failed");
  });
  const label = document.querySelector("[data-status-label]");
  if (label) {
    label.textContent = payload.detail || stageLabels[current] || current;
  }
  renderSteps(payload);
  renderActivity(payload);
  const spinner = document.querySelector("[data-spinner]");
  if (spinner) {
    const running = current !== "completed" && current !== "failed";
    spinner.classList.toggle("hidden", !running || current === "investigating");
  }
  const badge = document.querySelector(".page-intro .badge");
  if (badge && current) {
    badge.textContent = current;
    badge.className = "badge badge-" + current;
  }
}

function renderActivity(payload) {
  const panel = document.querySelector("[data-model-activity]");
  if (!panel) {
    return;
  }
  const items = payload.activity || [];
  const investigating = payload.status === "investigating";
  panel.classList.toggle("hidden", !investigating && items.length === 0);
  const thought = [...items].reverse().find((item) => item.kind === "thought" || item.kind === "action" || item.kind === "status");
  const thoughtNode = panel.querySelector("[data-model-thought]");
  if (thoughtNode) {
    thoughtNode.textContent = thought ? thought.text : "The investigator is deciding what to read.";
  }
  const list = panel.querySelector("[data-model-actions]");
  if (list) {
    list.replaceChildren();
    for (const item of items.filter((entry) => entry.kind === "action")) {
      const row = document.createElement("li");
      row.textContent = item.text;
      list.append(row);
    }
  }
  const thinking = panel.querySelector("[data-model-thinking]");
  if (thinking) {
    thinking.classList.toggle("hidden", !investigating);
  }
}

function renderSteps(payload) {
  const container = document.querySelector("[data-progress-steps]");
  if (!container || !payload.steps) {
    return;
  }
  const current = payload.status || "";
  const steps = payload.steps || {};
  const open = new Set([...container.querySelectorAll("details[open]")].map((item) => item.dataset.step));
  const seen = new Set([...container.querySelectorAll("details")].map((item) => item.dataset.step));
  container.replaceChildren();
  stageOrder.forEach((stage) => {
    const step = steps[stage];
    if (!step) {
      return;
    }
    const lines = step.lines && step.lines.length ? step.lines : ["No additional detail was recorded."];
    const details = document.createElement("details");
    details.className = "step";
    details.dataset.step = stage;
    const isCurrent = stage === current && current !== "completed" && current !== "failed";
    if (open.has(stage) || (isCurrent && !seen.has(stage))) {
      details.open = true;
    }
    const summary = document.createElement("summary");
    const name = document.createElement("span");
    name.textContent = stageLabels[stage] || stage;
    summary.append(name, document.createTextNode(step.summary || ""));
    const list = document.createElement("ul");
    for (const line of lines) {
      const item = document.createElement("li");
      item.textContent = line;
      list.append(item);
    }
    details.append(summary, list);
    container.append(details);
  });
}

const root = document.querySelector("[data-poll-status]");
if (root) {
  paintProgress({
    status: root.dataset.current,
    detail: document.querySelector("[data-status-label]")?.textContent || "",
  });
  if (root.dataset.terminal === "no") {
    const tick = async () => {
      const response = await fetch(root.dataset.pollStatus, { headers: { Accept: "application/json" } });
      if (!response.ok) {
        window.setTimeout(tick, 1200);
        return;
      }
      const payload = await response.json();
      paintProgress(payload);
      if (payload.status === "completed" || payload.status === "failed") {
        window.location.reload();
        return;
      }
      window.setTimeout(tick, 1200);
    };
    window.setTimeout(tick, 1200);
  }
}

document.querySelectorAll("[data-copy]").forEach((button) => {
  button.addEventListener("click", async () => {
    const block = button.closest(".sql-snippet");
    const pre = block ? block.querySelector("pre") : null;
    const text = pre ? pre.textContent : "";
    if (pre) {
      const range = document.createRange();
      range.selectNodeContents(pre);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    }
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = "Copied";
    } catch (error) {
      button.textContent = "Selected";
    }
  });
});

document.querySelectorAll("[data-diff]").forEach((diff) => {
  const rows = [...diff.querySelectorAll(".is-changed")];
  let index = -1;
  const button = diff.querySelector("[data-next-change]");
  if (!button) {
    return;
  }
  button.addEventListener("click", () => {
    if (!rows.length) {
      return;
    }
    index = (index + 1) % rows.length;
    rows[index].scrollIntoView({ block: "center" });
  });
});

document.querySelectorAll("[data-issue-filters]").forEach((bar) => {
  const buttons = [...bar.querySelectorAll("[data-filter]")];
  const issues = [...bar.parentElement.querySelectorAll(".issue[data-group]")];
  buttons.forEach((button) => {
    button.addEventListener("click", () => {
      const group = button.dataset.filter;
      buttons.forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
      issues.forEach((issue) => {
        issue.hidden = group !== "all" && issue.dataset.group !== group;
      });
    });
  });
});
