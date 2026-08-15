// Chrome/Edge/Firefox generally record webm/opus; Safari records mp4/aac.
// The backend uses ffmpeg to normalize either format to WAV.
let mediaRecorder = null;
let chunks = [];
let recordedBlob = null;
let timerId = null;
let previewUrl = null;

const $ = (id) => document.getElementById(id);

function showTab(which) {
  $("panelRecord").classList.toggle("hidden", which !== "record");
  $("panelUpload").classList.toggle("hidden", which !== "upload");
  $("tabRecord").classList.toggle("active", which === "record");
  $("tabUpload").classList.toggle("active", which === "upload");
}

function pickMime() {
  const preferences = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/mp4",
  ];
  if (!window.MediaRecorder) return "";
  return preferences.find((type) => MediaRecorder.isTypeSupported(type)) || "";
}

async function startRec() {
  if (!navigator.mediaDevices || !window.MediaRecorder) {
    $("recStatus").textContent =
      "Recording is not supported here; use the upload tab.";
    return;
  }

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (error) {
    $("recStatus").textContent = "mic blocked: " + error.message;
    return;
  }

  chunks = [];
  recordedBlob = null;
  const mime = pickMime();
  try {
    mediaRecorder = new MediaRecorder(
      stream,
      mime ? { mimeType: mime } : undefined,
    );
  } catch (error) {
    stream.getTracks().forEach((track) => track.stop());
    $("recStatus").textContent = "could not start recorder: " + error.message;
    return;
  }

  mediaRecorder.ondataavailable = (event) => {
    if (event.data.size) chunks.push(event.data);
  };
  mediaRecorder.onstop = () => {
    recordedBlob = new Blob(chunks, { type: mediaRecorder.mimeType });
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(recordedBlob);
    $("preview").src = previewUrl;
    $("preview").classList.remove("hidden");
    stream.getTracks().forEach((track) => track.stop());
    $("recStatus").textContent =
      "recorded " +
      (recordedBlob.size / 1024).toFixed(0) +
      " KB (" +
      (mediaRecorder.mimeType || "default codec") +
      ")";
  };

  mediaRecorder.start();
  const startedAt = Date.now();
  timerId = setInterval(() => {
    $("recStatus").textContent =
      "recording… " + ((Date.now() - startedAt) / 1000).toFixed(0) + "s";
  }, 250);
  $("btnStart").disabled = true;
  $("btnStop").disabled = false;
}

function stopRec() {
  if (mediaRecorder && mediaRecorder.state !== "inactive") {
    mediaRecorder.stop();
  }
  clearInterval(timerId);
  timerId = null;
  $("btnStart").disabled = false;
  $("btnStop").disabled = true;
}

function extFor(type) {
  if (!type) return "webm";
  if (type.includes("mp4")) return "mp4";
  if (type.includes("ogg")) return "ogg";
  return "webm";
}

function textCell(row, value, unit = "") {
  const cell = document.createElement("td");
  cell.textContent = value == null ? "—" : String(value) + unit;
  row.appendChild(cell);
}

function showSuccess(payload) {
  const box = $("result");
  const person = payload.person;
  const properties = payload.properties;
  box.className = "ok";
  box.replaceChildren();

  const heading = document.createElement("strong");
  heading.textContent = `Submission #${payload.submission_id} stored.`;
  box.append(heading, document.createElement("br"));
  box.append(
    person.matched_existing
      ? `Matched existing person #${person.person_id} (${person.name_on_file}) via phone.`
      : `Created new person #${person.person_id}.`,
  );

  const table = document.createElement("table");
  const header = document.createElement("tr");
  [
    "duration",
    "sample rate",
    "bitrate",
    "loudness",
    "SNR (rough)",
    "quality",
  ].forEach((label) => {
    const th = document.createElement("th");
    th.textContent = label;
    header.appendChild(th);
  });
  const values = document.createElement("tr");
  textCell(values, properties.duration_sec, " s");
  textCell(values, properties.sample_rate_khz, " kHz");
  textCell(values, properties.bitrate_kbps, " kbps");
  textCell(values, properties.loudness_db, " dBFS");
  textCell(values, properties.snr_db, " dB");
  textCell(
    values,
    properties.quality_label +
      (properties.clipping ? " ⚠ clipping" : ""),
  );
  table.append(header, values);
  box.appendChild(table);
}

async function submitAudio() {
  const name = $("name").value.trim();
  const phone = $("phone").value.trim();
  const uploaded = $("fileInput").files[0];
  const recordMode = !$("panelRecord").classList.contains("hidden");
  const box = $("result");
  const fail = (message) => {
    box.className = "err";
    box.classList.remove("hidden");
    box.textContent = message;
  };

  if (!name || !phone) return fail("Name and phone are required.");
  if (recordMode && !recordedBlob) {
    return fail("Record something first.");
  }
  if (!recordMode && !uploaded) {
    return fail("Choose an audio file first.");
  }

  const form = new FormData();
  form.append("name", name);
  form.append("phone", phone);
  if (recordMode) {
    form.append("kind", "recorded");
    form.append(
      "audio",
      recordedBlob,
      "recording." + extFor(recordedBlob.type),
    );
  } else {
    form.append("kind", "uploaded");
    form.append("audio", uploaded, uploaded.name);
  }

  $("btnSubmit").disabled = true;
  box.className = "";
  box.classList.remove("hidden");
  box.textContent = "Uploading + analyzing…";
  try {
    const response = await fetch("/api/submissions", {
      method: "POST",
      body: form,
    });
    let payload;
    try {
      payload = await response.json();
    } catch {
      throw new Error(`server returned HTTP ${response.status}`);
    }
    if (!response.ok) {
      fail(payload.error || `HTTP ${response.status}`);
      return;
    }
    showSuccess(payload);
  } catch (error) {
    fail("Network error: " + error.message);
  } finally {
    $("btnSubmit").disabled = false;
  }
}
