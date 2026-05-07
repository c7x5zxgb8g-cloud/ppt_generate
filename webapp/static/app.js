const state = {
  user: null,
  projects: [],
  selectedProjectId: null,
  activeJobId: null,
  pollTimer: null,
  previewIndex: 0,
};

const $ = (selector) => document.querySelector(selector);
const authView = $("#authView");
const appView = $("#appView");
const projectList = $("#projectList");
const projectDetail = $("#projectDetail");
const emptyState = $("#emptyState");
const toast = $("#toast");

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: options.body instanceof FormData ? {} : { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error?.message || `请求失败：${response.status}`);
  }
  return data;
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.remove("hidden");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.add("hidden"), 3200);
}

function setSession(user) {
  state.user = user;
  $("#accountName").textContent = user ? `${user.displayName} · ${user.role}` : "未登录";
  $("#logoutBtn").classList.toggle("hidden", !user);
  authView.classList.toggle("hidden", Boolean(user));
  appView.classList.toggle("hidden", !user);
}

async function loadMe() {
  const data = await api("/api/me");
  setSession(data.user);
  if (data.user) {
    await loadProjects();
  }
}

async function loadProjects() {
  const data = await api("/api/projects");
  state.projects = data.data;
  renderProjectList();
  if (state.selectedProjectId) {
    renderProjectDetail(state.projects.find((project) => project.id === state.selectedProjectId));
  }
}

function renderProjectList() {
  projectList.innerHTML = "";
  if (!state.projects.length) {
    const item = document.createElement("p");
    item.className = "path";
    item.textContent = "暂无项目";
    projectList.append(item);
    return;
  }

  for (const project of state.projects) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `project-item ${project.id === state.selectedProjectId ? "active" : ""}`;
    button.innerHTML = `<strong>${escapeHtml(project.name)}</strong><span>${project.canvasFormat} · ${project.info?.source_count || 0} 个源文件</span>`;
    button.addEventListener("click", () => {
      state.selectedProjectId = project.id;
      renderProjectList();
      renderProjectDetail(project);
    });
    projectList.append(button);
  }
}

function renderProjectDetail(project) {
  if (!project) {
    projectDetail.classList.add("hidden");
    emptyState.classList.remove("hidden");
    return;
  }
  emptyState.classList.add("hidden");
  projectDetail.classList.remove("hidden");
  $("#projectTitle").textContent = project.name;
  $("#projectFormat").textContent = project.canvasFormat;
  $("#projectMeta").textContent = `项目 ID ${project.id.slice(0, 8)} · 更新于 ${formatDate(project.updatedAt)}`;
  renderWorkflowStatus(project);
  updateActionAvailability(project);
  renderFiles(project);
  renderPreview(project);
  renderRecentJobs(project);
}

function renderWorkflowStatus(project) {
  const status = project.workflowStatus || {
    label: "待导入",
    message: "请先上传文档或导入 URL。",
    sourceCount: 0,
    svgCount: 0,
    exportCount: 0,
  };
  $("#projectStatusLabel").textContent = status.label;
  $("#projectStatusLabel").dataset.phase = status.phase || "empty";
  $("#projectStatusMessage").textContent = status.message;
  $("#sourceCount").textContent = `${status.sourceCount || 0} 源材料`;
  $("#svgCount").textContent = `${status.svgCount || 0} SVG`;
  $("#exportCount").textContent = `${status.exportCount || 0} 导出`;
  const runtime = project.aiRuntime || {};
  const capabilities = runtime.capabilities || {};
  const capabilityText = [
    capabilities.selfRepair ? "自修复" : null,
    capabilities.imageSearch ? "搜图" : null,
    capabilities.imageGeneration ? "生图" : null,
    capabilities.imagePromptRefinement ? "提示词精修" : null,
  ].filter(Boolean).join("/");
  $("#aiRunnerStatus").textContent = runtime.ready
    ? `AI ${runtime.runner || "api"} · ${runtime.model || "已配置"}${capabilityText ? ` · ${capabilityText}` : ""}`
    : "AI 未配置";
  $("#aiRunnerStatus").classList.toggle("runtime-missing", !runtime.ready);
}

function updateActionAvailability(project) {
  const svgCount = project.workflowStatus?.svgCount || 0;
  const sourceCount = project.workflowStatus?.sourceCount || 0;
  const runtimeReady = project.aiRuntime?.ready !== false;
  document.querySelectorAll("[data-job]").forEach((button) => {
    const job = button.dataset.job;
    const requiresSvg = job === "quality_check" || job === "postprocess" || job === "export";
    const requiresSource = job === "generate_ppt";
    const missingRuntime = job === "generate_ppt" && !runtimeReady;
    button.disabled = (requiresSvg && svgCount === 0) || (requiresSource && sourceCount === 0) || missingRuntime;
    button.title = missingRuntime ? "请先配置文本模型 API" : button.disabled ? "源材料或 SVG 页面就绪后可用" : "";
  });
}

function renderRecentJobs(project) {
  const jobs = project.recentJobs || [];
  if (!jobs.length) {
    $("#jobStatus").textContent = "暂无任务";
    $("#jobLog").textContent = "还没有运行校验、检查、后处理或导出任务。";
    return;
  }
  const latest = jobs[0];
  $("#jobStatus").textContent = `${latest.type} · ${latest.status} · ${latest.stage}`;
  $("#jobLog").textContent = formatJobForDisplay(latest, project);
}

function formatJobForDisplay(job, project = null) {
  const parts = [];
  parts.push(`任务：${job.type}`);
  parts.push(`状态：${job.status} · ${job.stage}`);
  parts.push(`创建：${job.createdAt}`);
  parts.push(`更新：${job.updatedAt}`);
  if (job.result) {
    parts.push("");
    parts.push("结果：");
    parts.push(JSON.stringify(job.result, null, 2));
  }
  if (job.log) {
    parts.push("");
    parts.push("日志：");
    parts.push(sanitizeDisplayLog(job.log, project));
  }
  return parts.join("\n");
}

function sanitizeDisplayLog(text, project = null) {
  let sanitized = String(text || "");
  sanitized = sanitized.replaceAll("/Users/shable/AI/ppt-master", "[系统目录]");
  sanitized = sanitized.replace(/\/Users\/[^\s"']+/g, "[本地路径]");
  return sanitized;
}

function fileList(items, type) {
  if (!items.length) {
    return "<li>暂无</li>";
  }
  return items
    .map((file) => {
      const name = escapeHtml(file.name);
      if (type === "exports") {
        const href = `/api/projects/${state.selectedProjectId}/downloads/${encodeURIComponent(file.name)}`;
        return `<li><a href="${href}">${name}</a></li>`;
      }
      return `<li>${name}</li>`;
    })
    .join("");
}

function renderFiles(project) {
  $("#sourceFiles").innerHTML = fileList(project.files.sources, "sources");
  const svgFiles = dedupeByName([...project.files.svgOutput, ...project.files.svgFinal]);
  $("#svgFiles").innerHTML = fileList(svgFiles, "svg");
  $("#exportFiles").innerHTML = fileList(project.files.exports, "exports");
}

function dedupeByName(items) {
  const seen = new Set();
  return items.filter((item) => {
    if (seen.has(item.name)) return false;
    seen.add(item.name);
    return true;
  });
}

function renderPreview(project) {
  const slides = project.previewSlides || [];
  const image = $("#slidePreview");
  const empty = $("#previewEmpty");
  const meta = $("#previewMeta");
  const prev = $("#prevSlideBtn");
  const next = $("#nextSlideBtn");

  if (!slides.length) {
    state.previewIndex = 0;
    image.removeAttribute("src");
    image.classList.add("hidden");
    empty.classList.remove("hidden");
    meta.textContent = "暂无可预览页面";
    empty.textContent = project.workflowStatus?.sourceCount
      ? "源材料已就绪，尚未生成 SVG 页面"
      : "生成 SVG 后即可预览";
    prev.disabled = true;
    next.disabled = true;
    return;
  }

  state.previewIndex = Math.min(Math.max(state.previewIndex, 0), slides.length - 1);
  const slide = slides[state.previewIndex];
  image.src = `${slide.url}?v=${encodeURIComponent(slide.name)}`;
  image.alt = `${slide.name} 预览`;
  image.classList.remove("hidden");
  empty.classList.add("hidden");
  meta.textContent = `${state.previewIndex + 1} / ${slides.length} · ${slide.name} · ${slide.source}`;
  prev.disabled = state.previewIndex === 0;
  next.disabled = state.previewIndex === slides.length - 1;
}

function movePreview(delta) {
  const project = state.projects.find((item) => item.id === state.selectedProjectId);
  if (!project?.previewSlides?.length) return;
  state.previewIndex += delta;
  renderPreview(project);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatDate(value) {
  if (!value) return "未知时间";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

async function submitAuth(event) {
  event.preventDefault();
  const mode = event.submitter?.dataset.mode || "login";
  const form = new FormData(event.currentTarget);
  const payload = {
    email: form.get("email"),
    password: form.get("password"),
    displayName: form.get("displayName"),
  };
  const data = await api(`/api/auth/${mode}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  setSession(data.user);
  await loadProjects();
}

async function createProject(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  const data = await api("/api/projects", {
    method: "POST",
    body: JSON.stringify({
      name: form.get("name"),
      canvasFormat: form.get("canvasFormat"),
    }),
  });
  state.selectedProjectId = data.data.id;
  formElement.reset();
  await loadProjects();
  showToast("项目已创建");
}

async function uploadSources(event) {
  event.preventDefault();
  if (!state.selectedProjectId) return;
  const formElement = event.currentTarget;
  const formData = new FormData(formElement);
  setImportBusy(true, "正在上传并转换源材料，请不要关闭页面。");
  try {
    const data = await api(`/api/projects/${state.selectedProjectId}/sources`, {
      method: "POST",
      body: formData,
    });
    const index = state.projects.findIndex((project) => project.id === data.data.id);
    if (index >= 0) state.projects[index] = data.data;
    renderProjectList();
    renderProjectDetail(data.data);
    renderImportSummary(data.summary);
    formElement.reset();
    showToast("源材料已导入");
  } catch (error) {
    setImportBusy(false, `导入失败：${error.message}`, "error");
    throw error;
  }
}

function setImportBusy(isBusy, message, tone = "running") {
  const form = $("#uploadForm");
  const button = $("#importSubmitBtn");
  const status = $("#importStatus");
  button.disabled = isBusy;
  button.textContent = isBusy ? "导入中..." : "导入";
  form.querySelectorAll("input").forEach((input) => {
    input.disabled = isBusy;
  });
  status.className = `import-status ${tone}`;
  status.textContent = message;
}

function renderImportSummary(summary) {
  const archived = summary?.archived?.length || 0;
  const markdown = summary?.markdown?.length || 0;
  const skipped = summary?.skipped?.length || 0;
  const notes = summary?.notes?.length || 0;
  const parts = [`已归档 ${archived} 个文件`, `生成/识别 ${markdown} 个 Markdown`];
  if (notes) parts.push(`${notes} 条说明`);
  if (skipped) parts.push(`${skipped} 个未处理`);
  setImportBusy(false, parts.join("，"), skipped ? "warning" : "success");
}

async function startJob(type) {
  if (!state.selectedProjectId) return;
  const data = await api(`/api/projects/${state.selectedProjectId}/jobs`, {
    method: "POST",
    body: JSON.stringify({ type }),
  });
  state.activeJobId = data.data.id;
  $("#jobLog").textContent = "";
  $("#jobStatus").textContent = `${data.data.type} · ${data.data.status}`;
  pollJob();
}

async function pollJob() {
  if (!state.activeJobId) return;
  window.clearTimeout(state.pollTimer);
  const data = await api(`/api/jobs/${state.activeJobId}`);
  const job = data.data;
  const project = state.projects.find((item) => item.id === state.selectedProjectId);
  $("#jobStatus").textContent = `${job.type} · ${job.status} · ${job.stage}`;
  $("#jobLog").textContent = job.log
    ? sanitizeDisplayLog(job.log, project)
    : JSON.stringify(job.result || {}, null, 2);
  $("#jobLog").scrollTop = $("#jobLog").scrollHeight;

  if (job.status === "queued" || job.status === "running") {
    state.pollTimer = window.setTimeout(pollJob, 1200);
    return;
  }

  await loadProjects();
  showToast(job.status === "succeeded" ? "任务完成" : "任务失败，查看日志");
}

$("#authForm").addEventListener("submit", (event) => {
  submitAuth(event).catch((error) => showToast(error.message));
});

$("#logoutBtn").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST", body: "{}" });
  setSession(null);
});

$("#createProjectForm").addEventListener("submit", (event) => {
  createProject(event).catch((error) => showToast(error.message));
});

$("#uploadForm").addEventListener("submit", (event) => {
  uploadSources(event).catch((error) => showToast(error.message));
});

$("#refreshBtn").addEventListener("click", () => {
  loadProjects().catch((error) => showToast(error.message));
});

document.querySelectorAll("[data-job]").forEach((button) => {
  button.addEventListener("click", () => startJob(button.dataset.job).catch((error) => showToast(error.message)));
});

$("#prevSlideBtn").addEventListener("click", () => movePreview(-1));
$("#nextSlideBtn").addEventListener("click", () => movePreview(1));

loadMe().catch((error) => showToast(error.message));
