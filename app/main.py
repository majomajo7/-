from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "500"))
MAX_SPLITS = int(os.getenv("MAX_SPLITS", "30"))
ACCESS_KEY = os.getenv("ACCESS_KEY", "").strip()
CHUNK_SIZE = 1024 * 1024

ALLOWED_EXTENSIONS = {
    ".mp3",
    ".m4a",
    ".wav",
    ".webm",
    ".ogg",
    ".opus",
    ".aac",
    ".flac",
    ".mp4",
    ".mpeg",
    ".mpga",
    ".mov",
}

app = FastAPI(title="GPT Audio Splitter", version="1.0.0")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


def safe_stem(filename: str | None) -> str:
    raw_stem = Path(filename or "audio").stem
    cleaned = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", raw_stem).strip("._-")
    return cleaned[:80] or "audio"


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=60 * 60,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail="서버에 FFmpeg가 설치되어 있지 않습니다.") from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="오디오 처리 시간이 너무 길어 중단되었습니다.") from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or "FFmpeg 처리 오류").strip()
        raise HTTPException(status_code=422, detail=f"오디오를 처리하지 못했습니다: {message[-800:]}") from exc


def probe_duration(input_path: Path) -> float:
    result = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(input_path),
        ]
    )
    try:
        payload = json.loads(result.stdout)
        duration = float(payload["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="오디오 재생 시간을 확인할 수 없습니다.") from exc

    if duration <= 0:
        raise HTTPException(status_code=422, detail="재생 시간이 0초인 파일은 처리할 수 없습니다.")
    return duration


def split_audio(input_path: Path, output_dir: Path, stem: str, split_count: int, duration: float) -> list[Path]:
    # N개 파일을 보장하기 위해 N-1개의 절단 시점을 명시합니다.
    cut_points = [duration * i / split_count for i in range(1, split_count)]
    segment_times = ",".join(f"{point:.6f}" for point in cut_points)
    output_pattern = output_dir / f"{stem}_part_%02d_of_{split_count:02d}.mp3"

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(input_path),
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "48k",
        "-f",
        "segment",
        "-segment_times",
        segment_times,
        "-segment_start_number",
        "1",
        "-reset_timestamps",
        "1",
        str(output_pattern),
    ]
    run_command(command)

    parts = sorted(output_dir.glob(f"{stem}_part_*_of_{split_count:02d}.mp3"))
    if len(parts) != split_count:
        raise HTTPException(
            status_code=500,
            detail=f"분할 결과가 {split_count}개여야 하지만 {len(parts)}개가 생성되었습니다.",
        )
    return parts


def create_zip(zip_path: Path, parts: list[Path], source_name: str, duration: float) -> None:
    readme = (
        "GPT 오디오 분할 결과\n"
        f"원본 파일: {source_name}\n"
        f"총 길이: {duration:.2f}초\n"
        f"분할 개수: {len(parts)}개\n"
        "출력 형식: MP3 / mono / 16 kHz / 48 kbps\n\n"
        "각 MP3 파일을 순서대로 ChatGPT에 업로드하고 다음과 같이 요청하세요:\n"
        "'이 오디오를 원문 언어로 정확히 전사해줘. 불명확한 부분은 [불명확]으로 표시해줘.'\n"
    )
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for part in parts:
            archive.write(part, arcname=part.name)
        archive.writestr("사용안내.txt", readme)


def cleanup_directory(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html = (APP_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    html = html.replace("{{MAX_UPLOAD_MB}}", str(MAX_UPLOAD_MB))
    html = html.replace("{{MAX_SPLITS}}", str(MAX_SPLITS))
    html = html.replace("{{REQUIRES_KEY}}", "true" if ACCESS_KEY else "false")
    return HTMLResponse(html)


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.post("/api/split")
async def split_endpoint(
    audio: UploadFile = File(...),
    split_count: int = Form(...),
    access_key: str = Form(""),
) -> FileResponse:
    if ACCESS_KEY and access_key != ACCESS_KEY:
        raise HTTPException(status_code=401, detail="접속 암호가 올바르지 않습니다.")

    if split_count < 2 or split_count > MAX_SPLITS:
        raise HTTPException(status_code=400, detail=f"분할 개수는 2~{MAX_SPLITS} 사이여야 합니다.")

    suffix = Path(audio.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise HTTPException(status_code=415, detail=f"지원하지 않는 형식입니다. 지원 형식: {allowed}")

    job_dir = Path(tempfile.mkdtemp(prefix="audio_split_"))
    input_path = job_dir / f"input{suffix}"
    output_dir = job_dir / "parts"
    output_dir.mkdir()

    try:
        total_bytes = 0
        with input_path.open("wb") as destination:
            while True:
                chunk = await audio.read(CHUNK_SIZE)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > MAX_UPLOAD_MB * 1024 * 1024:
                    raise HTTPException(
                        status_code=413,
                        detail=f"파일은 최대 {MAX_UPLOAD_MB}MB까지 업로드할 수 있습니다.",
                    )
                destination.write(chunk)

        if total_bytes == 0:
            raise HTTPException(status_code=400, detail="빈 파일은 처리할 수 없습니다.")

        duration = probe_duration(input_path)
        if duration < split_count * 0.5:
            raise HTTPException(status_code=400, detail="분할 개수에 비해 오디오가 너무 짧습니다.")

        stem = safe_stem(audio.filename)
        parts = split_audio(input_path, output_dir, stem, split_count, duration)
        zip_path = job_dir / f"{stem}_split_{split_count}.zip"
        create_zip(zip_path, parts, audio.filename or "audio", duration)

        headers = {
            "X-Audio-Duration": f"{duration:.3f}",
            "X-Split-Count": str(split_count),
        }
        return FileResponse(
            path=zip_path,
            media_type="application/zip",
            filename=zip_path.name,
            headers=headers,
            background=BackgroundTask(cleanup_directory, job_dir),
        )
    except HTTPException:
        cleanup_directory(job_dir)
        raise
    except Exception as exc:
        cleanup_directory(job_dir)
        raise HTTPException(status_code=500, detail=f"예상하지 못한 오류가 발생했습니다: {exc}") from exc
    finally:
        await audio.close()
