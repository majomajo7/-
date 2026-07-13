(() => {
  const config = window.APP_CONFIG;
  const form = document.getElementById("splitForm");
  const fileInput = document.getElementById("audioFile");
  const dropZone = document.getElementById("dropZone");
  const fileCard = document.getElementById("fileCard");
  const fileName = document.getElementById("fileName");
  const fileMeta = document.getElementById("fileMeta");
  const removeFile = document.getElementById("removeFile");
  const splitCount = document.getElementById("splitCount");
  const minusButton = document.getElementById("minusButton");
  const plusButton = document.getElementById("plusButton");
  const submitButton = document.getElementById("submitButton");
  const partDuration = document.getElementById("partDuration");
  const partSize = document.getElementById("partSize");
  const estimateCard = document.querySelector(".estimate-card");
  const progressPanel = document.getElementById("progressPanel");
  const progressTitle = document.getElementById("progressTitle");
  const progressPercent = document.getElementById("progressPercent");
  const progressBar = document.getElementById("progressBar");
  const progressMessage = document.getElementById("progressMessage");
  const resultPanel = document.getElementById("resultPanel");
  const resultMessage = document.getElementById("resultMessage");
  const errorPanel = document.getElementById("errorPanel");
  const keyField = document.getElementById("keyField");

  let selectedFile = null;
  let audioDurationSeconds = null;
  let busy = false;

  if (config.requiresKey) keyField.classList.remove("hidden");

  const formatBytes = (bytes) => {
    if (!Number.isFinite(bytes)) return "";
    const units = ["B", "KB", "MB", "GB"];
    let value = bytes;
    let index = 0;
    while (value >= 1024 && index < units.length - 1) {
      value /= 1024;
      index += 1;
    }
    return `${value.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
  };

  const formatDuration = (seconds) => {
    if (!Number.isFinite(seconds)) return "길이 확인 불가";
    const rounded = Math.max(0, Math.round(seconds));
    const hours = Math.floor(rounded / 3600);
    const minutes = Math.floor((rounded % 3600) / 60);
    const secs = rounded % 60;
    if (hours > 0) return `${hours}시간 ${minutes}분 ${secs}초`;
    if (minutes > 0) return `${minutes}분 ${secs}초`;
    return `${secs}초`;
  };

  const estimate = () => {
    const count = Math.max(2, Math.min(config.maxSplits, Number(splitCount.value) || 2));
    splitCount.value = count;
    estimateCard.classList.remove("safe", "warning");
    if (!audioDurationSeconds) {
      partDuration.textContent = "파일을 선택하면 계산됩니다";
      partSize.textContent = "GPT 업로드용 48 kbps MP3";
      return;
    }
    const perPart = audioDurationSeconds / count;
    const estimatedMb = perPart * 48000 / 8 / 1024 / 1024;
    partDuration.textContent = `파일당 약 ${formatDuration(perPart)}`;
    partSize.textContent = `예상 용량 약 ${estimatedMb.toFixed(1)}MB · 총 ${count}개`;
    estimateCard.classList.add(estimatedMb <= 24 ? "safe" : "warning");
    if (estimatedMb > 24) partSize.textContent += " · GPT 업로드 제한을 확인하세요";
  };

  const loadDuration = (file) => {
    audioDurationSeconds = null;
    const audio = document.createElement("audio");
    const objectUrl = URL.createObjectURL(file);
    audio.preload = "metadata";
    audio.onloadedmetadata = () => {
      audioDurationSeconds = Number.isFinite(audio.duration) ? audio.duration : null;
      URL.revokeObjectURL(objectUrl);
      fileMeta.textContent = `${formatBytes(file.size)} · ${formatDuration(audioDurationSeconds)}`;
      estimate();
    };
    audio.onerror = () => {
      URL.revokeObjectURL(objectUrl);
      fileMeta.textContent = `${formatBytes(file.size)} · 서버에서 길이 확인`;
      estimate();
    };
    audio.src = objectUrl;
  };

  const chooseFile = (file) => {
    if (!file || busy) return;
    selectedFile = file;
    fileName.textContent = file.name;
    fileMeta.textContent = `${formatBytes(file.size)} · 길이 확인 중`;
    fileCard.classList.remove("hidden");
    submitButton.disabled = false;
    resultPanel.classList.add("hidden");
    errorPanel.classList.add("hidden");
    loadDuration(file);
  };

  const clearFile = () => {
    if (busy) return;
    selectedFile = null;
    audioDurationSeconds = null;
    fileInput.value = "";
    fileCard.classList.add("hidden");
    submitButton.disabled = true;
    estimate();
  };

  dropZone.addEventListener("click", () => !busy && fileInput.click());
  dropZone.addEventListener("keydown", (event) => {
    if (!busy && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      fileInput.click();
    }
  });
  fileInput.addEventListener("change", () => chooseFile(fileInput.files[0]));
  removeFile.addEventListener("click", clearFile);

  ["dragenter", "dragover"].forEach((eventName) => {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      if (!busy) dropZone.classList.add("dragging");
    });
  });
  ["dragleave", "drop"].forEach((eventName) => {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropZone.classList.remove("dragging");
    });
  });
  dropZone.addEventListener("drop", (event) => chooseFile(event.dataTransfer.files[0]));

  minusButton.addEventListener("click", () => {
    if (busy) return;
    splitCount.value = Math.max(2, (Number(splitCount.value) || 2) - 1);
    estimate();
  });
  plusButton.addEventListener("click", () => {
    if (busy) return;
    splitCount.value = Math.min(config.maxSplits, (Number(splitCount.value) || 2) + 1);
    estimate();
  });
  splitCount.addEventListener("input", estimate);
  splitCount.addEventListener("change", estimate);

  const filenameFromDisposition = (header) => {
    if (!header) return "audio_split.zip";
    const utf8Match = header.match(/filename\*=UTF-8''([^;]+)/i);
    if (utf8Match) return decodeURIComponent(utf8Match[1]);
    const basicMatch = header.match(/filename="?([^";]+)"?/i);
    return basicMatch ? basicMatch[1] : "audio_split.zip";
  };

  const setProgress = (title, percentText, width, message, processing = false) => {
    progressTitle.textContent = title;
    progressPercent.textContent = percentText;
    progressBar.style.width = `${width}%`;
    progressBar.classList.toggle("processing", processing);
    progressMessage.textContent = message;
  };

  const showError = (message) => {
    busy = false;
    progressPanel.classList.add("hidden");
    resultPanel.classList.add("hidden");
    errorPanel.textContent = message;
    errorPanel.classList.remove("hidden");
    submitButton.disabled = !selectedFile;
  };

  const readError = async (response, fallback) => {
    try {
      const payload = await response.json();
      return payload.detail || fallback;
    } catch (_) {
      return fallback;
    }
  };

  const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

  const uploadChunkWithRetry = async (url, blob, token, index) => {
    let lastError = null;
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      try {
        const response = await fetch(url, {
          method: "POST",
          headers: {
            "Content-Type": "application/octet-stream",
            "X-Upload-Token": token,
            "X-Chunk-Index": String(index),
          },
          body: blob,
        });
        if (response.ok) return;
        const message = await readError(response, `업로드 조각 전송 실패 (HTTP ${response.status})`);
        if (response.status >= 400 && response.status < 500 && response.status !== 409) {
          throw new Error(message);
        }
        lastError = new Error(message);
      } catch (error) {
        lastError = error;
      }
      if (attempt < 3) await sleep(800 * attempt);
    }
    throw lastError || new Error("업로드 조각을 전송하지 못했습니다.");
  };

  const downloadFinishedFile = (jobId, uploadToken) => new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/upload/finish");
    xhr.responseType = "blob";
    xhr.setRequestHeader("Content-Type", "application/json");
    xhr.timeout = 1000 * 60 * 100;

    xhr.onload = async () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        const url = URL.createObjectURL(xhr.response);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = filenameFromDisposition(xhr.getResponseHeader("Content-Disposition"));
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        setTimeout(() => URL.revokeObjectURL(url), 5000);
        resolve();
        return;
      }
      let message = `처리 중 오류가 발생했습니다. (HTTP ${xhr.status})`;
      try {
        const text = await xhr.response.text();
        const payload = JSON.parse(text);
        if (payload.detail) message = payload.detail;
      } catch (_) {}
      reject(new Error(message));
    };
    xhr.onerror = () => reject(new Error("서버와 통신하지 못했습니다. 잠시 후 다시 시도해 주세요."));
    xhr.ontimeout = () => reject(new Error("처리 시간이 초과되었습니다. 서버 사양을 높이거나 더 짧은 파일로 시도해 주세요."));
    xhr.send(JSON.stringify({ job_id: jobId, upload_token: uploadToken }));
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selectedFile || busy) return;

    busy = true;
    errorPanel.classList.add("hidden");
    resultPanel.classList.add("hidden");
    progressPanel.classList.remove("hidden");
    submitButton.disabled = true;
    setProgress("업로드 준비 중", "0%", 0, "큰 파일도 안정적으로 전송하도록 여러 조각으로 나눠 업로드합니다.");

    try {
      const startResponse = await fetch("/api/upload/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filename: selectedFile.name,
          size: selectedFile.size,
          split_count: Number(splitCount.value),
          access_key: document.getElementById("accessKey").value || "",
        }),
      });
      if (!startResponse.ok) {
        throw new Error(await readError(startResponse, `업로드 준비 실패 (HTTP ${startResponse.status})`));
      }
      const session = await startResponse.json();
      const chunkSize = session.chunk_size;
      const totalChunks = Math.ceil(selectedFile.size / chunkSize);

      for (let index = 0; index < totalChunks; index += 1) {
        const start = index * chunkSize;
        const end = Math.min(start + chunkSize, selectedFile.size);
        const blob = selectedFile.slice(start, end);
        await uploadChunkWithRetry(
          `/api/upload/${session.job_id}/chunk`,
          blob,
          session.upload_token,
          index,
        );
        const percentage = Math.round((end / selectedFile.size) * 100);
        setProgress(
          "파일 업로드 중",
          `${percentage}%`,
          percentage,
          `${index + 1} / ${totalChunks} 조각 전송 완료`,
        );
      }

      setProgress(
        "오디오 변환 및 분할 중",
        "처리 중",
        45,
        "창을 닫지 마세요. 2시간 녹음은 무료 서버에서 수 분 이상 걸릴 수 있습니다.",
        true,
      );
      await downloadFinishedFile(session.job_id, session.upload_token);

      busy = false;
      progressPanel.classList.add("hidden");
      resultPanel.classList.remove("hidden");
      resultMessage.textContent = `${splitCount.value}개의 MP3가 들어 있는 ZIP 파일을 저장했습니다.`;
      submitButton.disabled = false;
    } catch (error) {
      showError(error instanceof Error ? error.message : "알 수 없는 오류가 발생했습니다.");
    }
  });

  estimate();
})();
