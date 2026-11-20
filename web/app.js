"use strict";
const main = document.querySelector("main");
const announce = document.querySelector("#announce");
const draft = { mode: "link", url: "", file: null, preview: "", key: null, busy: false, settings: {} };
const activeStates = new Set(["queued", "running"]);
const stages = [
  ["Prepare source", "Check the video and audio tracks."],
  ["Find highlights", "Look for distinct moments in motion, audio and speech."],
  ["Assemble clips", "Choose source ranges and prepare framing and captions."],
  ["Render your clips", "Each verified file appears as soon as it’s ready."],
];
const labels = { queued: "Queued", running: "Processing", ready: "Ready", partial: "Some clips ready",
  failed: "Failed", cancelled: "Cancelled", no_matches: "No strong clips" };
const escapeHTML = value => String(value ?? "").replace(/[&<>"']/g, char =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
const seconds = ms => (ms / 1000).toFixed(3).replace(/\.?0+$/, "");
const uid = () => crypto.randomUUID();
const runPath = id => `#/runs/${id}`;
const api = async (path, options) => {
  const response = await fetch(`/api/${path}`, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "This action failed. Try again.");
  return result;
};
const post = (path, body = {}) => api(path, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

document.querySelector(".skip").addEventListener("click", event => {
  event.preventDefault();
  main.focus();
});

let turn = 0;
let timer;
function online(value) {
  document.querySelector("#worker-label").textContent = value ? "Local worker" : "Worker offline";
}
function alertMessage(error, target = main.querySelector("[role=alert]")) {
  if (target) target.textContent = error.message;
}
function artwork(url, caption, note = "") {
  return `<figure class="preview"><div class="preview-frame">
    <img src="${url}" alt="${escapeHTML(caption)}"><span class="source-tag badge">${escapeHTML(note)}</span>
    </div><figcaption class="meta"><span>${escapeHTML(caption)}</span></figcaption></figure>`;
}
function formatOptions(values = {}) {
  const options = (items, selected) => items.map(s => `<option${s === selected ? " selected" : ""}>${s}</option>`).join("");
  return `<div class="options">
    <div class="field"><label for="ratio">Aspect ratio</label><select name="ratio" id="ratio">${options(["9:16","16:9","1:1","4:5","3:4"], values.ratio)}</select></div>
    <div class="field"><label for="quality">Resolution</label><select name="quality" id="quality">${options(["HD","UHD"], values.quality)}</select></div>
    <div class="field"><label for="framing">Framing</label><select name="framing" id="framing">
    <option value="fit">Fit entire frame</option><option value="crop"${values.framing === "crop" ? " selected" : ""}>Center crop</option></select></div>
    <label class="check"><input type="checkbox" name="captions"${values.captions !== false ? " checked" : ""}> Speech captions</label>
    </div>`;
}
function formSettings(form) {
  const data = new FormData(form);
  return { ratio: data.get("ratio"), quality: data.get("quality"), framing: data.get("framing"), captions: data.has("captions") };
}
function generator() {
  main.innerHTML = `<section class="split generator">
    <div class="form-wrap"><h1>Generate <em>clips.</em></h1>
    <p class="lede">Turn a video link or upload into a set of ready-to-post highlights.</p>
    <form id="generate">
      <div class="modes" role="group" aria-label="Source type">
        <button type="button" data-mode="link" aria-pressed="${draft.mode === "link"}">↗ Video link</button>
        <button type="button" data-mode="upload" aria-pressed="${draft.mode === "upload"}">↑ Upload file</button>
      </div>
      <div class="field" ${draft.mode !== "link" ? "hidden" : ""}>
        <label class="sr-only" for="link">Direct HTTPS video link</label>
        <input id="link" type="url" placeholder="Paste a direct video link" value="${escapeHTML(draft.url)}" autocomplete="off" ${draft.mode !== "link" ? "disabled" : "required"}>
      </div>
      <div class="field" ${draft.mode !== "upload" ? "hidden" : ""}><label class="sr-only" for="upload">Choose a video</label>
        <input id="upload" type="file" accept="video/*,.mkv,.mov,.webm"><span class="meta wrap" id="file-name">${escapeHTML(draft.file?.name)}</span>
      </div>
      <div class="actions generate-actions"><button class="primary" type="submit"${draft.busy ? " disabled" : ""}>${draft.busy ? "Importing video…" : "Generate clips →"}</button>
      <span class="meta" id="preset-label"></span></div>
      <details class="settings"><summary>Export settings</summary>${formatOptions(draft.settings)}</details>
      <p role="alert" class="error"></p>
    </form></div><div id="generator-preview"></div>
  </section><section class="section"><div class="heading-row"><h2>Recent clips</h2><a class="button" href="#/runs">My runs →</a></div><div id="recent"></div></section>`;
  const form = main.querySelector("form");
  const preview = main.querySelector("#generator-preview");
  const showPreview = () => {
    preview.innerHTML = draft.mode === "upload" && draft.preview
      ? `<figure class="preview"><div class="preview-frame"><video controls preload="metadata" src="${draft.preview}" aria-label="Uploaded source preview"></video></div><figcaption class="meta wrap">${escapeHTML(draft.file.name)}</figcaption></figure>`
      : artwork("/example.jpg", "Source preview · coastal study", "EXAMPLE FRAME");
  };
  const preset = () => {
    draft.settings = formSettings(form);
    main.querySelector("#preset-label").textContent = `${draft.settings.ratio} · ${draft.settings.quality === "HD" ? "1080p" : "UHD"}${draft.settings.captions ? " · Auto captions" : ""}`;
  };
  form.querySelector("#upload").disabled = draft.mode !== "upload";
  showPreview();
  preset();
  form.addEventListener("change", event => {
    if (event.target.id === "upload" && event.target.files.length) {
      if (draft.preview) URL.revokeObjectURL(draft.preview);
      draft.file = event.target.files[0];
      draft.preview = URL.createObjectURL(draft.file);
      main.querySelector("#file-name").textContent = draft.file.name;
      showPreview();
    }
    draft.key = null;
    preset();
  });
  form.querySelector("#link").addEventListener("input", event => {
    draft.url = event.target.value;
    draft.key = null;
  });
  form.querySelectorAll("[data-mode]").forEach(button => button.addEventListener("click", () => {
    if (draft.busy || draft.mode === button.dataset.mode) return;
    draft.mode = button.dataset.mode;
    draft.key = null;
    for (const mode of form.querySelectorAll("[data-mode]")) mode.setAttribute("aria-pressed", mode === button);
    for (const field of ["link", "upload"]) {
      const input = form.querySelector("#" + field);
      input.disabled = field !== draft.mode;
      input.parentElement.hidden = input.disabled;
    }
    showPreview();
  }));
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (draft.busy) return;
    const submit = form.querySelector("[type=submit]");
    const error = form.querySelector("[role=alert]");
    error.textContent = "";
    if (draft.mode === "upload" && !draft.file) {
      alertMessage(new Error("Choose a video first."), error);
      form.querySelector("#upload").focus();
      return;
    }
    draft.busy = submit.disabled = true;
    const key = draft.key ||= uid();
    const options = formSettings(form);
    submit.textContent = "Importing video…";
    try {
      const source = draft.mode === "link" ? await post("sources", { url: draft.url.trim() })
        : await api("sources", { method: "POST", body: draft.file,
          headers: { "Content-Type": "application/octet-stream", "X-File-Name": encodeURIComponent(draft.file.name) } });
      const job = await post("jobs", { source: source.id, key, settings: options });
      if (draft.preview) URL.revokeObjectURL(draft.preview);
      Object.assign(draft, { url: "", file: null, preview: "", key: null });
      location.hash = runPath(job.id);
    } catch (err) {
      alertMessage(err, error);
      submit.focus();
    } finally {
      draft.busy = submit.disabled = false;
      submit.textContent = "Generate clips →";
    }
  });
  const recent = main.querySelector("#recent");
  api("jobs").then(async jobs => {
    const groups = await Promise.all(jobs.filter(job => job.ready).slice(0, 3)
      .map(async job => ({ ...job, clips: await api(`jobs/${job.id}/clips`) })));
    const clips = groups.flatMap(job => job.clips.filter(clip => clip.state === "ready")
      .map(clip => ({ clip, job }))).slice(0, 3);
    recent.innerHTML = clips.length ? `<div class="grid">${clips.map(({ clip, job }) => card(clip, job.name)).join("")}</div>`
      : '<div class="empty">Finished clips will appear here after your first run.</div>';
  }).catch(err => { recent.textContent = err.message; });
}
function clipTitle(clip, source) {
  return `${source.replace(/\.[a-z0-9]{2,5}$/i, "")} · ${clip.parent ? "Copy" : "Pick"} ${String(clip.rank).padStart(2,"0")}`;
}
function card(clip, name) {
  const root = `/api/clips/${clip.id}`;
  const title = escapeHTML(clipTitle(clip, name));
  const ready = clip.state === "ready";
  return `<article class="card" data-clip="${clip.id}">
    ${ready ? `<div class="card-art"><video controls playsinline preload="metadata" src="${root}/play"${clip.artifact.poster ? ` poster="${root}/poster"` : ""} aria-label="${title}"></video>
    <span class="badge">${clip.parent ? "COPY" : "PICK"} ${String(clip.rank).padStart(2,"0")}</span></div>` : ""}
    <div class="card-body"><h3 class="wrap">${title}</h3>
    <p class="meta">${clip.range_label} · ${seconds(clip.end_ms - clip.start_ms)}s · ${clip.options.ratio}
    ${ready ? ` · ${clip.artifact.width} × ${clip.artifact.height}${clip.artifact.upscale ? " · Upscaled" : ""}` : ""}</p>
    <p>${escapeHTML(clip.reason)}</p>
    ${ready ? `<div class="actions"><a class="button" href="${root}/download" aria-label="Download ${title}">↓ Download</a>
    <a href="${runPath(clip.job)}/${clip.id}">Details ↗</a></div>`
    : clip.state === "failed" ? `<p class="error">${escapeHTML(clip.error)}</p><button type="button" data-retry="${clip.id}">Retry clip</button>`
    : "<p>Waiting to render</p>"}</div></article>`;
}
function runSkeleton() {
  main.innerHTML = `<a class="back" href="#/runs">← My runs</a>
    <div class="heading-row"><div><h1 id="run-heading">Loading <em>run.</em></h1><p class="muted wrap" id="run-caption"></p></div>
    <div class="actions" id="run-actions"></div></div><p role="alert" class="error"></p>
    <section id="run-progress" class="section split" hidden><div id="source-preview"></div>
    <div><h2>Run progress</h2><ol class="progress"></ol></div></section>
    <section id="run-status" hidden class="status"></section>
    <section id="run-results" class="section" hidden><div class="heading-row"><h2 id="results-heading">Ranked picks</h2><span class="meta">MP4 · H.264</span></div><div class="grid"></div></section>`;
}
function sync(container, html) {
  if (container.dataset.content !== html) {
    container.innerHTML = html;
    container.dataset.content = html;
  }
}
function runView(job) {
  const active = activeStates.has(job.state);
  const ready = job.clips.filter(clip => clip.state === "ready").length;
  const displayedState = active && !job.online ? "Waiting for the worker" : labels[job.state];
  const caption = `${job.name} · ${Math.round(job.metadata.duration_ms / 1000)}s source · ${ready ? `${ready} ready to download` : displayedState}`;
  const title = active ? "Making your <em>clips.</em>" : ready ? "Your <em>clips.</em>" : "Run <em>summary.</em>";
  sync(main.querySelector("#run-heading"), title);
  main.querySelector("#run-caption").textContent = caption;
  sync(main.querySelector("#run-actions"), `${active ? '<button type="button" id="cancel">Cancel run</button>' : '<a class="button" href="#/">New run</a>'}
    ${ready ? `<a class="button primary" href="/api/jobs/${job.id}/download">↓ Download all</a>` : ""}`);
  const progress = main.querySelector("#run-progress");
  progress.hidden = !active;
  if (active) {
    sync(main.querySelector("#source-preview"), job.stage >= 2
      ? artwork(`/api/jobs/${job.id}/preview`, job.name, "SOURCE FRAME")
      : artwork("/example.jpg", "Source preview will appear after preparation", "EXAMPLE FRAME"));
    sync(progress.querySelector("ol"), stages.map(([name, text], index) => {
      const stage = index + 1;
      return `<li${job.stage === stage ? ' aria-current="step"' : ""}><span class="step" aria-hidden="true">${job.stage > stage ? "✓" : stage}</span>
        <div><strong>${name}</strong><p>${job.stage > stage ? "Complete" : text}</p></div></li>`;
    }).join(""));
  }
  const status = main.querySelector("#run-status");
  const message = {
    partial: "Verified clips are available. Retry a failed clip from its card.",
    failed: job.error || "The worker could not export these clips. Retry any failed clip below.",
    cancelled: "Generation stopped. Previously verified clips remain available.",
    no_matches: "No strong clips found. Try footage with distinct actions or complete spoken moments.",
  }[job.state] || (active && !job.online ? "Waiting for the worker. This run resumes when the worker restarts." : "");
  status.hidden = !message && !job.notice;
  sync(status, `${message ? `<p>${escapeHTML(message)}</p>` : ""}${job.notice ? `<p class="muted">${escapeHTML(job.notice)}</p>` : ""}`);
  const results = main.querySelector("#run-results");
  results.hidden = !job.clips.length;
  const grid = results.querySelector(".grid");
  // Preserve playing videos and keyboard focus when adjacent clips become ready.
  for (const clip of job.clips) {
    const existing = grid.querySelector(`[data-clip="${clip.id}"]`);
    const html = card(clip, job.name);
    if (existing?.dataset.renderState === clip.state) continue;
    const template = document.createElement("template");
    template.innerHTML = html;
    const node = template.content.firstElementChild;
    node.dataset.renderState = clip.state;
    if (existing) existing.replaceWith(node);
    else grid.append(node);
  }
  const messageKey = `${job.id}:${displayedState}:${job.stage}:${ready}`;
  if (announce.dataset.last !== messageKey) {
    announce.dataset.last = messageKey;
    announce.textContent = `${displayedState}.${active && job.stage ? " " + stages[job.stage-1][0] + "." : ""} ${ready} clips ready.`;
  }
}
function detail(job, clip) {
  const title = escapeHTML(clipTitle(clip, job.name));
  const ready = clip.state === "ready";
  main.innerHTML = `<a class="back" href="${runPath(job.id)}">← Back to clips</a>
    <div class="heading-row"><div><h1 class="wrap">${title}</h1><p class="meta">${clip.range_label} · ${seconds(clip.end_ms - clip.start_ms)}s</p></div>
    ${ready ? `<a class="button primary" href="/api/clips/${clip.id}/download">↓ Download clip</a>` : ""}</div>
    <p role="alert" class="error"></p>
    ${ready ? `<section class="section split detail">
      <video class="detail-video" controls playsinline preload="metadata" src="/api/clips/${clip.id}/play"${clip.artifact.poster ? ` poster="/api/clips/${clip.id}/poster"` : ""} aria-label="${title}"></video>
      <div><h2>Why this clip</h2><p>${escapeHTML(clip.reason)}</p>
      ${clip.artifact.captions ? `<div class="actions"><a class="button" href="/api/clips/${clip.id}/srt">↓ SRT</a><a class="button" href="/api/clips/${clip.id}/vtt">↓ VTT</a></div>` : ""}
      <div class="section"><h2>Optional adjustments</h2><form id="revision">
      <fieldset class="options"><legend>Source range</legend>
      <div class="field"><label for="start">Start (seconds)</label><input id="start" type="number" name="start" min="0" max="${seconds(job.metadata.duration_ms)}" step="0.001" value="${seconds(clip.start_ms)}" required></div>
      <div class="field"><label for="end">End (seconds)</label><input id="end" type="number" name="end" min="0" max="${seconds(job.metadata.duration_ms)}" step="0.001" value="${seconds(clip.end_ms)}" required></div></fieldset>
      <fieldset><legend>Export</legend>${formatOptions(clip.options)}</fieldset>
      <button class="primary" type="submit">Save a copy</button></form></div></div></section>`
      : `<div class="status">${escapeHTML(clip.error || "This copy is waiting to render.")}
      ${clip.state === "failed" ? `<button type="button" data-retry="${clip.id}">Retry clip</button>` : ""}</div>`}`;
  main.querySelector("#revision")?.addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.target;
    const submit = form.querySelector("button");
    submit.disabled = true;
    try {
      const values = new FormData(form);
      const result = await post(`clips/${clip.id}/revisions`, {
        start_ms: Math.round(Number(values.get("start")) * 1000),
        end_ms: Math.round(Number(values.get("end")) * 1000),
        settings: formSettings(form),
      });
      location.hash = `${runPath(job.id)}/${result.id}`;
    } catch (error) { alertMessage(error); }
    finally { submit.disabled = false; }
  });
}
async function poll(id, clipId, token, signature = "") {
  let next = true;
  try {
    const job = await api(`jobs/${id}`);
    if (token !== turn) return;
    main.querySelector("[role=alert]").textContent = "";
    online(job.online);
    if (clipId) {
      const clip = job.clips.find(item => item.id === clipId);
      if (!clip) throw new Error("This clip could not be found.");
      if (signature !== clip.state) detail(job, clip);
      signature = clip.state;
      next = clip.state === "queued" || activeStates.has(job.state);
    } else {
      runView(job);
      next = activeStates.has(job.state);
    }
  } catch (error) {
    if (token !== turn) return;
    alertMessage(new Error(`${error.message} Checking connection again…`));
  }
  if (next && token === turn) timer = setTimeout(() => poll(id, clipId, token, signature), 1050);
}
main.addEventListener("click", async event => {
  const button = event.target.closest("button");
  if (!button || (!button.dataset.retry && button.id !== "cancel")) return;
  const [, , id, clipId] = location.hash.split("/");
  button.disabled = true;
  try {
    if (button.dataset.retry) await post(`clips/${button.dataset.retry}/retry`);
    else await post(`jobs/${id}/cancel`);
    clearTimeout(timer);
    await poll(id, clipId, ++turn);
  } catch (error) { alertMessage(error); }
  finally { button.disabled = false; }
});
async function route(navigate = false) {
  const token = ++turn;
  clearTimeout(timer);
  const [, section, id, clip] = location.hash.split("/");
  document.querySelector("#nav-generate").toggleAttribute("aria-current", section !== "runs");
  document.querySelector("#nav-runs").toggleAttribute("aria-current", section === "runs");
  document.querySelectorAll("nav [aria-current]").forEach(link => link.setAttribute("aria-current", "page"));
  api("health").then(status => online(status.online)).catch(() => online(false));
  if (section !== "runs") generator();
  else if (id) {
    runSkeleton();
    poll(id, clip, token);
  } else {
    main.innerHTML = '<h1>My <em>runs.</em></h1><p role="alert" class="error"></p><section class="section" id="history">Loading runs…</section>';
    api("jobs").then(jobs => {
      if (token !== turn) return;
      main.querySelector("#history").innerHTML = jobs.length ? jobs.map(job =>
        `<div class="run-row"><a href="${runPath(job.id)}">${escapeHTML(job.name)}</a><span class="meta">${labels[job.state]} · ${job.ready} ready</span></div>`
      ).join("") : '<div class="empty">No runs yet. <a href="#/">Generate your first clips</a>.</div>';
    }).catch(error => { if (token === turn) alertMessage(error); });
  }
  if (navigate) { window.scrollTo(0, 0); main.focus(); }
}
addEventListener("hashchange", () => route(true));
route();
