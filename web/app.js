"use strict";
const main = document.querySelector("main");
const announce = document.querySelector("#announce");
const draft = { mode: "link", url: "", file: null, preview: "", key: null, importLabel: "", importError: "", settings: {}, music: null, musicId: null };
const clipTargets = [3, 5, 10, 15, 20, 50, 100];
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
function sourceDuration(ms) {
  const total = Math.round(ms / 1000);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor(total / 60) % 60;
  const parts = [];
  if (hours) parts.push(hours + "h");
  if (minutes) parts.push(minutes + "m");
  if (total % 60 || !parts.length) parts.push(total % 60 + "s");
  return parts.join(" ");
}
function readySummary(ready, originals, requested) {
  const copies = ready - originals;
  const extra = copies ? ` · ${copies} edited ${copies === 1 ? "copy" : "copies"}` : "";
  return `${originals} of ${requested} requested clips ready${extra}`;
}
const uid = () => crypto.randomUUID();
const runPath = id => `#/runs/${id}`;
const api = async (path, options) => {
  let response;
  try {
    response = await fetch(`/api/${path}`, options);
  } catch (error) {
    if (!(error instanceof TypeError)) throw error;
    throw new Error("Lost connection to ClipFarm. Check that the local server is running, then retry.");
  }
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
  document.querySelector("#worker").classList.toggle("offline", !value);
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
function monitor() {
  return `<figure class="monitor">
    <div class="monitor-top"><span class="monitor-title">Source monitor</span><span class="monitor-kind">Example still</span></div>
    <div class="monitor-image"><img src="/surf-study.jpg" alt="A surfer carves across the face of a breaking wave.">
      <div class="crop-guide" aria-hidden="true"><span></span><span></span><span></span><span></span></div>
    </div>
    <figcaption><span>Surf study</span><button type="button" class="guide-switch" aria-pressed="true">9:16 guide</button></figcaption>
  </figure>`;
}
function formatOptions(values = {}, selectedMusic = null) {
  const options = (items, selected) => items.map(s => `<option${s === selected ? " selected" : ""}>${s}</option>`).join("");
  return `<div class="options">
    <div class="field"><label for="ratio">Aspect ratio</label><select name="ratio" id="ratio">${options(["9:16","16:9","1:1","4:5","3:4"], values.ratio)}</select></div>
    <div class="field"><label for="quality">Resolution</label><select name="quality" id="quality">${options(["HD","UHD"], values.quality)}</select></div>
    <div class="field"><label for="framing">Framing</label><select name="framing" id="framing">
    <option value="fit">Fit entire frame</option><option value="track"${(values.framing || "track") === "track" ? " selected" : ""}>Follow subject</option><option value="crop"${values.framing === "crop" ? " selected" : ""}>Center crop</option></select></div>
    <label class="check"><input type="checkbox" name="captions"${values.captions !== false ? " checked" : ""}> Speech captions</label>
    <div class="field music-field"><label for="music">Background music <span>(optional)</span></label>
      <input id="music" name="music" type="file" accept="audio/*,.mp3,.wav,.m4a,.ogg,.flac">
      <span class="field-hint">Music lowers under the source audio.</span>
      ${selectedMusic ? `<button type="button" id="clear-music">Remove ${escapeHTML(selectedMusic.name)}</button>` : ""}
      ${values.music_id ? '<label class="check"><input type="checkbox" name="remove_music"> Remove existing soundtrack</label>' : ""}
    </div></div>`;
}
const lengths = [10,15,20,30,40,45,60,90,120,180,300];
function exportSize(settings) {
  const base = {"9:16":[1080,1920],"16:9":[1920,1080],"1:1":[1080,1080],"4:5":[1080,1350],"3:4":[1080,1440]};
  return base[settings.ratio].map(x => x * (settings.quality === "UHD" ? 2 : 1)).join(" × ");
}
function durationLabel(pref) {
  if (!pref) return "Auto length";
  if (pref.mode === "range") return `${pref.min_ms/1000}–${pref.max_ms/1000}s range`;
  return `${pref.target_ms/1000}s ${pref.mode}`;
}
function durationControls() {
  const state = draft.durationForm || {};
  const options = (items, value) => items.map(([id,name]) =>
    `<option value="${id}"${String(id) === String(value) ? " selected" : ""}>${name}</option>`).join("");
  const clock = (key,label,initial) => `<fieldset data-clock="${key}"><legend>${label}</legend>${["Minutes","Seconds"].map((unit,i) => {
    const name = key + unit;
    return `<label>${unit}<input type="number" name="${name}" min="0" max="${i ? 59 : 5}" required value="${Number(state[name] ?? (i ? initial : 0))}"></label>`;
  }).join("")}</fieldset>`;
  return `<fieldset class="duration-controls"><legend>Clip duration</legend><label>Length<select name="lengthPreset" aria-label="Length">${options([["auto","Auto"],...lengths.map(n=>[n,n>=120 && n%60===0 ? n/60+" min" : n+"s"]),["custom","Custom"]],state.lengthPreset)}</select></label>
    <label data-mode-field>Duration mode<select name="lengthMode" aria-label="Duration mode">${options([["target","Target"],["exact","Exact"],["range","Range"]],state.lengthMode)}</select></label>
    ${clock("target","Custom length",30)}${clock("min","Minimum",10)}${clock("max","Maximum",45)}</fieldset>`;
}
function readDuration(form) {
  const e = form.elements;
  const preset = e.lengthPreset.value, mode = e.lengthMode.value;
  const field = form.querySelector("[data-mode-field]");
  field.hidden = preset === "auto";
  e.lengthMode.disabled = field.hidden;
  for (const clock of form.querySelectorAll("[data-clock]")) {
    clock.hidden = preset === "auto" || (clock.dataset.clock === "target" ? preset !== "custom" || mode === "range" : mode !== "range");
    clock.disabled = clock.hidden;
  }
  const ms = key => (Number(e[key+"Minutes"].value)*60 + Number(e[key+"Seconds"].value))*1000;
  const duration = preset === "auto" ? null : mode === "range" ? {mode,min_ms:ms("min"),max_ms:ms("max")} :
    {mode,target_ms:preset === "custom" ? ms("target") : Number(preset)*1000};
  const rangeInvalid = duration && mode === "range" && (duration.min_ms < 1000 || duration.max_ms > 300000 || duration.min_ms > duration.max_ms);
  e.maxSeconds.setCustomValidity(rangeInvalid ? "Choose an ordered range from 1 to 300 seconds." : "");
  e.targetSeconds.setCustomValidity(duration && mode !== "range" && (duration.target_ms < 1000 || duration.target_ms > 300000) ? "Choose 1 to 300 seconds." : "");
  draft.durationForm = Object.fromEntries(Array.from(form.querySelectorAll(".duration-controls input, .duration-controls select"), control => [control.name, control.value]));
  return duration ? {duration} : {};
}

function formSettings(form) {
  const data = new FormData(form);
  return { ...(form.elements.lengthPreset ? readDuration(form) : {}), ...(data.has("count") ? { count: Number(data.get("count")) } : {}),
    ...(data.has("selector") ? { selector: data.get("selector") } : {}),
    ratio: data.get("ratio"), quality: data.get("quality"), framing: data.get("framing"), captions: data.has("captions") };
}
function importLabel(message) {
  draft.importLabel = message;
  const form = main.querySelector("#generate");
  if (!form) return;
  form.querySelector(".generator-controls").disabled = Boolean(message);
  form.querySelector("[type=submit]").textContent = message || "Generate clips →";
  form.setAttribute("aria-busy", String(Boolean(message)));
  if (message && !message.endsWith("%")) announce.textContent = message;
}
function uploadVideo(file) {
  return new Promise((resolve, reject) => {
    const upload = new XMLHttpRequest();
    upload.open("POST", "/api/sources");
    upload.responseType = "json";
    upload.setRequestHeader("Content-Type", "application/octet-stream");
    upload.setRequestHeader("X-File-Name", encodeURIComponent(file.name));
    upload.upload.onprogress = event => {
      if (event.lengthComputable) importLabel(`Uploading video · ${Math.floor(event.loaded * 100 / event.total)}%`);
    };
    upload.upload.onload = () => importLabel("Checking video…");
    upload.onload = () => {
      if (upload.status >= 200 && upload.status < 300 && upload.response?.id) resolve(upload.response);
      else reject(new Error(upload.response?.error || "The video upload failed. Try again."));
    };
    upload.onerror = () => reject(new Error("The upload was interrupted. Reconnect and try again."));
    upload.send(file);
  });
}
async function soundtrack(file) {
  const uploaded = await api("music", { method: "POST", body: file, headers: {
    "Content-Type": "application/octet-stream", "X-File-Name": encodeURIComponent(file.name),
  } });
  return uploaded.id;
}
function generator() {
  main.innerHTML = `<section class="split generator">
    <div class="form-wrap"><h1>Clip <em>generator</em></h1>
    <p class="lede">Find the highlights in your footage. Cut, reframe, and caption them in one run.</p>
    <form id="generate"><fieldset class="generator-controls"><legend class="sr-only">New clip run</legend>
      <div class="modes" role="group" aria-label="Source type">
        <button type="button" data-mode="link" aria-pressed="${draft.mode === "link"}"><span class="link-icon" aria-hidden="true">↗</span> Video link</button>
        <button type="button" data-mode="upload" aria-pressed="${draft.mode === "upload"}"><span aria-hidden="true">↑</span> Upload file</button>
      </div>
      <div class="field" ${draft.mode !== "link" ? "hidden" : ""}>
        <label for="link">Public video link</label>
        <input id="link" type="text" inputmode="url" placeholder="Paste a public video URL…" value="${escapeHTML(draft.url)}" autocomplete="off" autocapitalize="none" spellcheck="false" aria-describedby="link-hint" required ${draft.mode !== "link" ? "disabled" : ""}>
        <span class="field-hint" id="link-hint">Paste a recording from YouTube, Twitch, Kick, Rumble or another supported site, or a direct HTTPS video link.</span>
      </div>
      <div class="field" ${draft.mode !== "upload" ? "hidden" : ""}><label for="upload">Choose a video</label>
        <input id="upload" type="file" accept="video/*,.mkv,.mov,.webm,.avi,.flv,.m4v,.ts,.m2ts,.mpeg,.mpg,.ogv" aria-describedby="upload-hint"><span class="meta wrap" id="file-name">${escapeHTML(draft.file?.name)}</span>
        <span class="field-hint" id="upload-hint">Choose a recording of any length. Keep this tab open until the upload finishes.</span>
      </div>
      ${durationControls()}
      <fieldset class="clip-target" aria-describedby="target-hint"><legend>Clip target</legend>
        <div class="target-choices">${clipTargets.map(count =>
          `<label><input type="radio" name="count" value="${count}"${count === (draft.settings.count || 3) ? " checked" : ""}><span>${count}</span></label>`).join("")}</div>
        <p class="field-hint" id="target-hint">ClipFarm exports up to your target, depending on how many distinct highlights it finds.</p>
      </fieldset>
      <details class="settings"><summary><span>Export settings</span><span class="meta" id="preset-label"></span></summary>${formatOptions(draft.settings, draft.music)}
        <div class="field"><label for="selector">Highlight selection</label>
        <select name="selector" id="selector"><option value="auto">General highlights</option>
          <option value="editorial"${draft.settings.selector === "editorial" ? " selected" : ""}>Experimental film-trailer model</option>
        </select></div></details>      <div class="actions generate-actions"><button class="primary" type="submit">Generate clips →</button>
      </div>

      </fieldset><p role="alert" class="error">${escapeHTML(draft.importError)}</p>
    </form></div><aside><div id="generator-preview"></div><div class="config-summary"><h2>Output</h2><p id="output-summary"></p></div></aside>
  </section><section class="section"><div class="heading-row"><h2>Recent clips</h2><a class="text-action" href="#/runs">View all runs <span aria-hidden="true">↗</span></a></div><div id="recent"></div></section>`;
  const form = main.querySelector("form");
  form.querySelector("#clear-music")?.addEventListener("click", event => {
    draft.music = draft.musicId = draft.key = null;
    form.elements.music.value = "";
    event.currentTarget.remove();
    form.elements.music.focus();
  });
  const preview = main.querySelector("#generator-preview");
  let guideVisible = true;
  const updateGuide = () => {
    const guide = preview.querySelector(".crop-guide");
    const toggle = preview.querySelector(".guide-switch");
    if (!guide || !toggle) return;
    const ratio = form.elements.ratio.value;
    guide.dataset.ratio = ratio;
    guide.hidden = !guideVisible;
    toggle.setAttribute("aria-pressed", String(guideVisible));
    toggle.textContent = ratio + " guide";
  };
  const showPreview = () => {
    preview.innerHTML = draft.mode === "upload" && draft.preview
      ? `<figure class="preview"><div class="preview-frame"><video controls preload="metadata" src="${draft.preview}" aria-label="Uploaded source preview"></video></div><figcaption class="meta wrap">${escapeHTML(draft.file.name)}</figcaption></figure>`
      : monitor();
    preview.querySelector(".guide-switch")?.addEventListener("click", () => {
      guideVisible = !guideVisible;
      updateGuide();
    });
    preview.querySelector("video")?.addEventListener("loadedmetadata", event => {
      const video = event.target;
      if (Number.isFinite(video.duration)) preview.querySelector("figcaption").textContent =
        draft.file.name + " · " + sourceDuration(Math.round(video.duration*1000)) + " · " + video.videoWidth + " × " + video.videoHeight;
    });
    updateGuide();
  };
  const preset = () => {
    draft.settings = formSettings(form);
    main.querySelector("#output-summary").textContent = [draft.settings.count+" clips",durationLabel(draft.settings.duration),draft.settings.ratio,exportSize(draft.settings),draft.settings.quality].join(" · ");
    updateGuide();
    main.querySelector("#preset-label").textContent = `${draft.settings.ratio} · ${draft.settings.quality === "HD" ? "1080p" : "UHD"}${draft.settings.captions ? " · Auto captions" : ""}`;
  };
  form.querySelector("#upload").disabled = draft.mode !== "upload";
  form.addEventListener("input", event => {
    if (event.target.name === "lengthMode" && event.target.value === "range") form.elements.lengthPreset.value = "custom";
    if (event.target.name === "lengthPreset" && event.target.value !== "custom" && form.elements.lengthMode.value === "range") form.elements.lengthMode.value = "target";
    draft.key = null;
    if (!draft.importLabel) preset();
  });
  showPreview();
  preset();
  importLabel(draft.importLabel);
  form.addEventListener("change", event => {
    if (event.target.id === "music") {
      draft.music = event.target.files[0] || null;
      draft.musicId = null;
      form.querySelector("#clear-music")?.remove();
    }
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
    if (draft.importLabel || draft.mode === button.dataset.mode) return;
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
    if (draft.importLabel) return;
    const error = form.querySelector("[role=alert]");
    error.textContent = "";
    if (draft.mode === "upload" && !draft.file) {
      alertMessage(new Error("Choose a video first."), error);
      form.querySelector("#upload").focus();
      return;
    }
    draft.importError = "";
    const key = draft.key ||= uid();
    const music = draft.music;
    const options = formSettings(form);
    importLabel(draft.mode === "link" ? "Finding and downloading video…" : "Uploading video…");
    try {
      let source;
      if (draft.mode === "link") {
        const value = draft.url.trim();
        const url = /^[a-z][a-z\d+.-]*:/i.test(value) ? value : "https://" + value.replace(/^\/\//, "");
        source = await post("sources", { url });
      } else {
        source = await uploadVideo(draft.file);
      }
      if (music) {
        importLabel("Importing music…");
        options.music_id = draft.musicId || await soundtrack(music);
        if (music === draft.music) draft.musicId = options.music_id;
      }
      importLabel("Creating run…");
      const job = await post("jobs", { source: source.id, key, settings: options });
      if (draft.preview) URL.revokeObjectURL(draft.preview);
      Object.assign(draft, { url: "", file: null, preview: "", key: null, music: null, musicId: null });
      location.hash = runPath(job.id);
    } catch (err) {
      draft.importError = err.message;
      alertMessage(err, main.querySelector("#generate [role=alert]"));
    } finally {
      importLabel("");
      if (draft.importError) main.querySelector("#generate [type=submit]")?.focus();
    }
  });
  const recent = main.querySelector("#recent");
  api("jobs").then(async jobs => {
    const groups = await Promise.all(jobs.filter(job => job.ready).slice(0, 3)
      .map(async job => ({ ...job, clips: await api(`jobs/${job.id}/clips`) })));
    const clips = groups.flatMap(job => job.clips.filter(clip => clip.state === "ready")
      .map(clip => ({ clip, job }))).slice(0, 3);
    recent.innerHTML = clips.length ? `<div class="grid">${clips.map(({ clip, job }) => card(clip, job.name)).join("")}</div>`
      : '<div class="empty"><span class="film-icon" aria-hidden="true"></span><div><strong>Your first clips will appear here</strong><p>Generate a run to play and download your picks.</p></div></div>';
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
    ${ready ? `<div class="card-art"><video controls playsinline preload="none" src="${root}/play"${clip.artifact.poster ? ` poster="${root}/poster"` : ""} aria-label="${title}"></video>
    <span class="badge">${seconds(clip.artifact.duration_ms)} s</span></div>` : ""}
    <div class="card-body"><h3 class="wrap">${title}</h3>
    <p class="meta">${clip.range_label} · ${clip.options.ratio}
    ${ready ? ` · ${clip.artifact.width} × ${clip.artifact.height}${clip.artifact.captions ? " · Captioned" : ""}${clip.artifact.upscale ? " · Upscaled" : ""}` : ""}</p>
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
    <section id="run-results" class="section" hidden><div class="heading-row"><h2 id="results-heading">Ranked picks</h2><label>Show <select id="clip-filter"><option value="all">All clips</option><option value="ready">Ready</option><option value="failed">Failed</option><option value="copies">Edited copies</option></select></label></div><div class="grid"></div></section>`;
}
function sync(container, html) {
  if (container.dataset.content !== html) {
    container.innerHTML = html;
    container.dataset.content = html;
  }
}
function runView(job) {
  const active = activeStates.has(job.state);
  const readyClips = job.clips.filter(clip => clip.state === "ready");
  const ready = readyClips.length;
  const requested = job.settings.count ?? 3;
  const originals = readyClips.filter(clip => !clip.parent).length;
  const summary = readySummary(ready, originals, requested);
  const displayedState = active && !job.online ? "Waiting for the worker" : labels[job.state];
  const caption = `${job.name} · ${sourceDuration(job.metadata.duration_ms)} source · ${summary} · ${durationLabel(job.settings.duration)} · ${job.settings.ratio} ${job.settings.quality} · ${labels[job.state]}`;
  const title = active ? "Making your <em>clips.</em>" : ready ? "Your <em>clips.</em>" : "Run <em>summary.</em>";
  sync(main.querySelector("#run-heading"), title);
  const filmSelection = job.settings.selector === "editorial" && job.stage >= 3 && job.clips.length;
  const selectedModel = filmSelection
    ? (job.selector.startsWith("rules-") ? " · General fallback" : " · Film-trailer model")
    : "";
  main.querySelector("#run-caption").textContent = caption + selectedModel;
  sync(main.querySelector("#run-actions"), `${active ? '<button type="button" id="cancel">Cancel run</button>' : '<a class="button" href="#/">New run</a>'}
    ${ready ? `<a class="button primary" href="/api/jobs/${job.id}/download">↓ Download all</a>` : ""}`);
  const progress = main.querySelector("#run-progress");
  progress.hidden = !active;
  if (active) {
    sync(main.querySelector("#source-preview"), job.stage >= 2
      ? artwork(`/api/jobs/${job.id}/preview`, job.name, "SOURCE FRAME")
      : '<div class="preview-placeholder">Preparing source preview…</div>' );
    sync(progress.querySelector("ol"), stages.map(([name, text], index) => {
      const stage = index + 1;
      return `<li${job.stage === stage ? ' aria-current="step"' : ""}><span class="step" aria-hidden="true">${job.stage > stage ? "✓" : stage}</span>
        <div><strong>${name}</strong><p>${job.stage > stage ? "Complete" : text}</p></div></li>`;
    }).join(""));
  }
  const status = main.querySelector("#run-status");
  const message = {
    ready: originals < requested ? "The run finished with fewer distinct highlights than requested." : "",
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
    if (existing?.dataset.renderState === clip.state) continue;
    const template = document.createElement("template");
    template.innerHTML = card(clip, job.name);
    const node = template.content.firstElementChild;
    node.dataset.renderState = clip.state;
    if (existing) existing.replaceWith(node);
    else grid.append(node);
  }
  const filter = results.querySelector("#clip-filter");
  const applyFilter = () => {
    for (const clip of job.clips) grid.querySelector(`[data-clip="${clip.id}"]`).hidden =
      filter.value !== "all" && (filter.value === "copies" ? !clip.parent : clip.state !== filter.value);
  };
  filter.onchange = applyFilter;
  applyFilter();
  const messageKey = `${job.id}:${displayedState}:${job.stage}:${ready}`;
  if (announce.dataset.last !== messageKey) {
    announce.dataset.last = messageKey;
    announce.textContent = `${displayedState}.${active && job.stage ? " " + stages[job.stage-1][0] + "." : ""} ${summary}.`;
  }
}
function reviewForm(job, clip) {
  if (clip.parent) return "";
  return `<details class="settings"><summary>Rate this pick</summary>
    <form id="rating">
      <fieldset class="options"><legend>Training feedback</legend>
        <div class="field"><label for="grade">Would you post this clip?</label>
        <select id="grade" name="label" required><option value="">Choose rating</option>
        <option value="0">0 · Unusable</option><option value="1">1 · Missing context</option>
        <option value="2">2 · Usable</option><option value="3">3 · Strong pick</option></select></div>
        <div class="field"><label for="family">Source genre</label><select name="family" id="family" required>
          <option value="">Choose genre</option>${["animation","gaming","interview","music","silent","sports","tutorial","vlog"]
            .map(family => `<option value="${family}">${family[0].toUpperCase()+family.slice(1)}</option>`).join("")}
        </select></div>
      </fieldset>
      <div class="field"><label for="rights">License or permission to train on this video</label>
        <input id="rights" name="rights" maxlength="250" required placeholder="e.g. My original recording"></div>
      <div class="actions"><button type="submit">Save rating</button>
        <a class="button" href="/api/jobs/${job.id}/ratings" download>Export run ratings (.jsonl)</a></div>
      <p class="muted" role="status"></p>
    </form></details>`;
}

function bindRating(clip) {
  const form = main.querySelector("#rating");
  if (!form) return;
  const disclosure = form.closest("details");
  const touched = new Set();
  const status = form.querySelector("[role=status]");
  const submit = form.querySelector("button");
  let loading = false;
  let edits = 0;
  form.addEventListener("input", event => {
    touched.add(event.target.name);
    edits++;
    status.textContent = "Unsaved changes.";
  });
  disclosure.addEventListener("toggle", async function readRating() {
    if (!disclosure.open || loading) return;
    loading = true;
    try {
      const { record } = await api(`clips/${clip.id}/rating`);
      for (const name of ["label", "family", "rights"]) {
        if (record && !touched.has(name)) form.elements[name].value = record[name];
      }
      disclosure.removeEventListener("toggle", readRating);
    } catch (error) { status.textContent = error.message; }
    finally { loading = false; }
  });
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (submit.disabled) return;
    const submittedVersion = edits;
    submit.disabled = true;
    status.textContent = "Saving rating…";
    try {
      await post(`clips/${clip.id}/rating`, { label: Number(form.elements.label.value),
        family: form.elements.family.value, rights: form.elements.rights.value });
      if (submittedVersion === edits) status.textContent = "Rating saved locally.";
    } catch (error) { status.textContent = error.message; }
    finally { submit.disabled = false; }
  });
}
function detail(job, clip) {
  const title = escapeHTML(clipTitle(clip, job.name));
  const ready = clip.state === "ready";
  const framing = clip.artifact?.framing_applied || clip.options.framing;
  const framingLabel = { fit: "Full frame", crop: "Center crop", track: "Subject tracking" }[framing];
  main.innerHTML = `<a class="back" href="${runPath(job.id)}">← Back to clips</a>
    <div class="heading-row"><div><h1 class="wrap">${title}</h1><p class="meta">${clip.range_label} · ${seconds(clip.end_ms - clip.start_ms)}s${ready ? ` · ${framingLabel}${clip.artifact.soundtrack ? " · Music mixed" : ""}` : ""}</p></div>
    ${ready ? `<a class="button primary" href="/api/clips/${clip.id}/download">↓ Download clip</a>` : ""}</div>
    <p role="alert" class="error"></p>
    ${ready ? `<section class="section split detail">
      <video class="detail-video" controls playsinline preload="metadata" src="/api/clips/${clip.id}/play"${clip.artifact.poster ? ` poster="/api/clips/${clip.id}/poster"` : ""} aria-label="${title}"></video>
      <div><h2>Why this clip</h2><p>${escapeHTML(clip.reason)}</p>
      ${clip.artifact.captions ? `<div class="actions"><a class="button" href="/api/clips/${clip.id}/srt">↓ SRT</a><a class="button" href="/api/clips/${clip.id}/vtt">↓ VTT</a></div>` : ""}
      <p class="field-hint">The player shows the verified export. Save a copy to render your changes.</p>
      <div class="section"><h2>Edit a copy</h2><form id="revision">
      <fieldset class="options"><legend>Source range</legend>
      <svg class="trim-strip" viewBox="0 0 100 4" preserveAspectRatio="none" aria-hidden="true"><rect width="100" height="4"/><rect id="trim-span" height="4"/></svg>
      <output id="trim-length" for="start end"></output>
      <div class="field"><label for="start">Start (seconds)</label><input id="start" type="number" name="start" min="0" max="${seconds(job.metadata.duration_ms)}" step="0.001" value="${seconds(clip.start_ms)}" required></div>
      <div class="field"><label for="end">End (seconds)</label><input id="end" type="number" name="end" min="0" max="${seconds(job.metadata.duration_ms)}" step="0.001" value="${seconds(clip.end_ms)}" required></div></fieldset>
      <fieldset><legend>Export</legend>${formatOptions(clip.options)}</fieldset>
      <button class="primary" type="submit">Save edited copy</button></form></div>${reviewForm(job, clip)}</div></section>`
      : `<div class="status">${escapeHTML(clip.error || "This copy is waiting to render.")}
      ${clip.state === "failed" ? `<button type="button" data-retry="${clip.id}">Retry clip</button>` : ""}</div>`}`;
  const editor = main.querySelector("#revision");
  if (editor) {
    const updateTrim = () => {
      const start = Math.round(editor.elements.start.valueAsNumber*1000);
      const end = Math.round(editor.elements.end.valueAsNumber*1000);
      const total = job.metadata.duration_ms;
      const valid = start >= 0 && end <= total && end-start >= 1000 && end-start <= 300000;
      editor.elements.end.setCustomValidity(valid ? "" : "Choose 1 to 300 seconds inside the source.");
      const strip = editor.querySelector("#trim-span");
      strip.setAttribute("x", valid ? 100*start/total : 0);
      strip.setAttribute("width", valid ? 100*(end-start)/total : 0);
      editor.querySelector("#trim-length").textContent = valid ? seconds(end-start)+" seconds selected" : "Check the source range";
    };
    editor.addEventListener("input",updateTrim);
    updateTrim();
  }
  bindRating(clip);
  main.querySelector("#revision")?.addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.target;
    const submit = form.querySelector("button");
    submit.disabled = true;
    try {
      const values = new FormData(form);
      const settings = formSettings(form);
      const music = form.elements.music.files[0];
      if (music) settings.music_id = await soundtrack(music);
      else if (values.has("remove_music")) settings.music_id = null;
      const result = await post(`clips/${clip.id}/revisions`, {
        start_ms: Math.round(Number(values.get("start")) * 1000),
        end_ms: Math.round(Number(values.get("end")) * 1000),
        settings,
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
  document.title = "ClipFarm — " + (section !== "runs" ? "Clip generator" : clip ? "Clip details" : id ? "Run" : "My runs");
  document.querySelector("#context").textContent = document.title.split(" — ")[1];
  if (section !== "runs") generator();
  else if (id) {
    runSkeleton();
    poll(id, clip, token);
  } else {
    main.innerHTML = '<div class="heading-row"><h1>My <em>runs</em></h1><a class="button primary" href="#/">New run →</a></div><p role="alert" class="error"></p><section class="section" id="history">Loading runs…</section>';
    api("jobs").then(jobs => {
      if (token !== turn) return;
      main.querySelector("#history").innerHTML = jobs.length ? jobs.map(job =>
        `<div class="run-row"><a href="${runPath(job.id)}">${escapeHTML(job.name)}</a><span class="run-meta"><span class="run-state">${labels[job.state]}</span><span class="meta">${readySummary(job.ready, job.ready_originals, job.requested)} · ${durationLabel(JSON.parse(job.duration_json || "null"))}</span></span></div>`
      ).join("") : '<div class="empty">No runs yet. <a href="#/">Generate your first clips</a>.</div>';
    }).catch(error => { if (token === turn) alertMessage(error); });
  }
  if (navigate) { window.scrollTo(0, 0); main.focus(); }
}
addEventListener("hashchange", () => route(true));
route();
