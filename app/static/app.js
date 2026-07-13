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
    if (estimatedMb > 24) {
      partSize.textContent += " · GPT API 기준 25MB에 근접/초과할 수 있음";
    }
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
    if (!file) return;
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
    selectedFile = null;
    audioDurationSeconds = null;
    fileInput.value = "";
    fileCard.classList.add("hidden");
    submitButton.disabled = true;
    estimate();
  };

  dropZone.addEventListener("click", () => fileInput.click());
  dropZone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      fileInput.click();
    }
  });
  fileInput.addEventListener("change", () => chooseFile(fileInput.files[0]));
  removeFile.addEventListener("click", clearFile);

  ["dragenter", "dragover"].forEach((eventName) => {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropZone.classList.add("dragging");
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
    splitCount.value = Math.max(2, (Number(splitCount.value) || 2) - 1);
    estimate();
  });
  plusButton.addEventListener("click", () => {
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

  const showError = (message) => {
    progressPanel.classList.add("hidden");
    resultPanel.classList.add("hidden");
    errorPanel.textContent = message;
    errorPanel.classList.remove("hidden");
    submitButton.disabled = !selectedFile;
  };

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!selectedFile) return;

    const data = new FormData();
    data.append("audio", selectedFile, selectedFile.name);
    data.append("split_count", splitCount.value);
    data.append("access_key", document.getElementById("accessKey").value || "");

    errorPanel.classList.add("hidden");
    resultPanel.classList.add("hidden");
    progressPanel.classList.remove("hidden");
    progressTitle.textContent = "파일 업로드 중";
    progressPercent.textContent = "0%";
    progressMessage.textContent = "업로드 후 서버에서 자동으로 변환·분할합니다.";
    progressBar.classList.remove("processing");
    progressBar.style.width = "0%";
    submitButton.disabled = true;

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/split");
    xhr.responseType = "blob";

    xhr.upload.onprogress = (progressEvent) => {
      if (!progressEvent.lengthComputable) return;
      const percentage = Math.round((progressEvent.loaded / progressEvent.total) * 100);
      progressBar.style.width = `${percentage}%`;
      progressPercent.textContent = `${percentage}%`;
      if (percentage >= 100) {
        progressTitle.textContent = "오디오 변환 및 분할 중";
        progressPercent.textContent = "처리 중";
        progressBar.style.width = "45%";
        progressBar.classList.add("processing");
        progressMessage.textContent = "창을 닫지 마세요. 긴 녹음은 처리에 시간이 더 필요합니다.";
      }
    };

    xhr.onload = async () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        const blob = xhr.response;
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = filenameFromDisposition(xhr.getResponseHeader("Content-Disposition"));
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        setTimeout(() => URL.revokeObjectURL(url), 2000);

        progressPanel.classList.add("hidden");
        resultPanel.classList.remove("hidden");
        resultMessage.textContent = `${splitCount.value}개의 MP3가 들어 있는 ZIP 파일을 저장했습니다.`;
        submitButton.disabled = false;
      } else {
        let message = `처리 중 오류가 발생했습니다. (HTTP ${xhr.status})`;
        try {
          const text = await xhr.response.text();
          const payload = JSON.parse(text);
          if (payload.detail) message = payload.detail;
        } catch (_) {}
        showError(message);
      }
    };

    xhr.onerror = () => showError("서버와 통신하지 못했습니다. 네트워크 상태를 확인해 주세요.");
    xhr.ontimeout = () => showError("처리 시간이 초과되었습니다. 파일을 더 많이 분할하거나 서버 사양을 높여 주세요.");
    xhr.timeout = 1000 * 60 * 100;
    xhr.send(data);
  });

  estimate();
})();
