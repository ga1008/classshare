// Evaluated only in probe-owned documents. The inspected game has no access to these pages.
async ({ mime, size, url, offline, timeoutMs }) => {
  const input = document.querySelector("input[type=file]");
  const file = offline ? input.files[0] : null;
  const controller = new AbortController();
  let blobUrl, element, font, timer;
  function issue(code) { throw Object.assign(new Error(code), {probeCode: code}); }
  async function readBytes() {
    let buf;
    if (offline) {
      if (!file || file.size !== size) issue("ASSET_BYTES");
      buf = await file.arrayBuffer();
    } else {
      let response;
      try { response = await fetch(url, {credentials:"omit", signal:controller.signal}); }
      catch { issue("ASSET_LOAD"); }
      if (!response.ok) issue("ASSET_LOAD");
      buf = await response.arrayBuffer();
    }
    if (buf.byteLength !== size) issue("ASSET_BYTES");
    return buf;
  }
  async function loadMedia(tag) {
    element = document.createElement(tag);
    if (!element.canPlayType(mime)) issue(tag === "audio" ? "AUDIO_UNSUPPORTED" : "VIDEO_UNSUPPORTED");
    if (!offline) element.crossOrigin = "anonymous";
    element.muted = true;
    element.preload = tag === "video" ? "auto" : "metadata";
    const ready = new Promise((resolve, reject) => {
      element.addEventListener(tag === "video" ? "loadeddata" : "loadedmetadata", resolve, {once:true});
      element.addEventListener("error", () => reject(Object.assign(new Error(), {
        probeCode:tag === "audio" ? "AUDIO_DECODE" : "VIDEO_DECODE"
      })), {once:true});
    });
    element.src = url;
    element.load();
    await ready;
    if (tag === "video" && (!element.videoWidth || !element.videoHeight)) issue("VIDEO_DECODE");
  }
  async function check() {
    if (mime.startsWith("image/")) {
      const img = new Image();
      if (offline) {
        // A displayed file: image can taint a canvas. Pixel access uses the same
        // local bytes via a File/Blob URL, without changing browser file policies.
        img.src = url;
        await img.decode().catch(() => issue("IMAGE_BROWSER_DECODE"));
        blobUrl = URL.createObjectURL(file);
        img.src = blobUrl;
      } else {
        img.crossOrigin = "anonymous";
        img.src = url;
      }
      await img.decode().catch(() => issue("IMAGE_BROWSER_DECODE"));
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = 1;
      const ctx = canvas.getContext("2d", {willReadFrequently:true});
      ctx.drawImage(img, 0, 0, 1, 1);
      try { ctx.getImageData(0, 0, 1, 1); }
      catch { issue("IMAGE_PIXEL_ACCESS"); }
    } else if (mime === "font/woff2") {
      const src = offline ? await readBytes() : "url(" + JSON.stringify(url) + ") format('woff2')";
      font = new FontFace("LanGameProbe", src);
      await font.load().catch(() => issue("FONT_DECODE"));
      document.fonts.add(font);
      if (font.status !== "loaded") issue("FONT_DECODE");
    } else if (mime.startsWith("audio/")) {
      await loadMedia("audio");
      // Cap PCM expansion before allocating a decoded buffer. At 8 kHz and eight
      // possible channels an ordinary 5-minute soundtrack fits the per-job budget.
      if (!Number.isFinite(element.duration) || element.duration * 8000 * 8 * 4 > 96 * 1024 * 1024)
        issue("ASSET_PROBE_BUDGET");
      const bytes = await readBytes();
      const audio = new OfflineAudioContext(1, 1, 8000);
      const data = await audio.decodeAudioData(bytes).catch(() => issue("AUDIO_DECODE"));
      if (!data.length || !data.numberOfChannels) issue("AUDIO_DECODE");
    } else if (mime.startsWith("video/")) {
      await loadMedia("video");
    } else if (mime === "application/json") {
      const bytes = await readBytes();
      try { JSON.parse(new TextDecoder("utf-8", {fatal:true}).decode(bytes)); }
      catch { issue("LEVEL_JSON_DECODE"); }
    } else {
      issue("ASSET_TYPE");
    }
  }
  try {
    await Promise.race([check(), new Promise((_,reject) => {
      timer = setTimeout(() => reject(Object.assign(new Error(),{probeCode:"ASSET_PROBE_BUDGET"})), timeoutMs);
    })]);
    return null;
  } catch (exc) {
    return exc.probeCode || "ASSET_DECODE";
  } finally {
    clearTimeout(timer);
    controller.abort();
    if (blobUrl) URL.revokeObjectURL(blobUrl);
    if (font) document.fonts.delete(font);
    if (element) { element.removeAttribute("src"); element.load(); }
  }
}
