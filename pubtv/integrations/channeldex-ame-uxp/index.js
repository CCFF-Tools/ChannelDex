const app = require("mediaencoder");
const {storage} = require("uxp");
const fs = require("fs");

// UXP does not provide Node's `path` module. The panel runs on macOS, so a
// tiny POSIX helper is sufficient and keeps paths native to the host runtime.
function joinPath(...parts) {
  return parts.reduce((joined, part, index) => {
    const value = String(part);
    if (index === 0) return value.replace(/\/+$/, "");
    return `${joined}/${value.replace(/^\/+/, "")}`;
  }, "");
}

function basename(filePath) {
  return String(filePath).split("/").pop();
}

const folderLabel = document.getElementById("folder");
const statusLabel = document.getElementById("status");
const logBox = document.getElementById("log");
let root = "";
const active = new Set();

function log(message) {
  logBox.textContent = `${new Date().toLocaleTimeString()} ${message}\n${logBox.textContent}`.slice(0, 4000);
}

async function writeJson(file, value) {
  await fs.writeFile(file, JSON.stringify(value), {encoding: "utf-8"});
}

async function setFolder(entry) {
  root = entry.nativePath;
  localStorage.setItem("bridgeFolderToken", await storage.localFileSystem.createPersistentToken(entry));
  folderLabel.textContent = root;
  await fs.mkdir(joinPath(root, "requests"), {recursive: true});
  await fs.mkdir(joinPath(root, "results"), {recursive: true});
  statusLabel.textContent = "Connected";
  log("Bridge connected.");
}

document.getElementById("choose").addEventListener("click", async () => {
  const entry = await storage.localFileSystem.getFolder();
  if (entry) await setFolder(entry);
});

async function heartbeat() {
  if (!root) return;
  const supported = Boolean(app.RenderQueue && app.RenderQueue.renderFile && app.RenderQueue.getJob);
  await writeJson(joinPath(root, "status.json"), {
    ready: supported, api: supported ? "RenderQueue.renderFile" : "unsupported",
    plugin_version: "1.0.0", updated_at: Date.now() / 1000
  });
  statusLabel.textContent = supported ? "Connected" : "AME Render Queue API unavailable";
}

async function finish(request, state, extra = {}) {
  await writeJson(joinPath(root, "results", `${request.id}.json`), {
    version: 1, id: request.id, state, updated_at: Date.now() / 1000, ...extra
  });
}

async function processRequest(fileName) {
  const requestPath = joinPath(root, "requests", fileName);
  let request;
  try {
    request = JSON.parse(await fs.readFile(requestPath, "utf-8"));
    if (!/^[a-f0-9]{32}$/.test(request.id || "") || `${request.id}.json` !== fileName ||
        !request.source || !request.preset || !request.output ||
        typeof request.expires_at !== "number") {
      throw new Error("Invalid ChannelDex request");
    }
    if (Date.now() / 1000 >= request.expires_at) {
      await finish(request, "failed", {error: "ChannelDex request expired before AME started."});
      return;
    }
    const queued = await app.RenderQueue.renderFile(request.source, request.preset, request.output);
    if (!queued || queued.result !== 0) throw new Error(queued?.message || "AME rejected the render request");
    const job = app.RenderQueue.getJob(queued.jobId);
    if (!job) throw new Error("AME did not return a render job");
    while (!(await job.isInFinalState())) {
      await finish(request, "encoding", {job_id: queued.jobId, job_group_id: queued.jobGroupId,
        progress: await job.getEncodeProgress(), message: await job.getEncodeProgressMessage()});
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    const status = await job.getStatus();
    if (status === job.STATUS_DONE || status === job.STATUS_DONE_WARNING) {
      await finish(request, "succeeded", {job_id: queued.jobId, job_group_id: queued.jobGroupId,
        output_files: await job.getOutputFilesAfterExport(), warning: status === job.STATUS_DONE_WARNING,
        log: await job.getLogOutput()});
      log(`Completed ${basename(request.source)}`);
    } else {
      await finish(request, "failed", {job_id: queued.jobId, job_group_id: queued.jobGroupId,
        error: await job.getErrorText() || "Adobe Media Encoder failed", log: await job.getLogOutput()});
    }
  } catch (error) {
    if (request?.id) await finish(request, "failed", {error: String(error)});
    log(`Failed ${fileName}: ${String(error)}`);
  } finally {
    try { if (request?.id) await fs.unlink(requestPath); } catch (error) {}
    active.delete(fileName);
  }
}

async function scan() {
  if (!root) return;
  try {
    for (const fileName of await fs.readdir(joinPath(root, "requests"))) {
      if (!fileName.endsWith(".json") || active.has(fileName)) continue;
      active.add(fileName);
      processRequest(fileName);
    }
  } catch (error) { log(`Bridge scan failed: ${String(error)}`); }
}

async function restore() {
  const token = localStorage.getItem("bridgeFolderToken");
  if (!token) return;
  try { await setFolder(await storage.localFileSystem.getEntryForPersistentToken(token)); }
  catch (error) { localStorage.removeItem("bridgeFolderToken"); }
}

restore();
setInterval(heartbeat, 2000);
setInterval(scan, 1000);
