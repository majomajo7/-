from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

APP_DIR = Path(__file__).resolve().parent
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "500"))
MAX_SPLITS = int(os.getenv("MAX_SPLITS", "30"))
ACCESS_KEY = os.getenv("ACCESS_KEY", "").strip()
UPLOAD_CHUNK_MB = int(os.getenv("UPLOAD_CHUNK_MB", "5"))
UPLOAD_CHUNK_BYTES = UPLOAD_CHUNK_MB * 1024 * 1024
JOB_MAX_AGE_SECONDS = 6 * 60 * 60
JOBS_ROOT = Path(tempfile.gettempdir()) / "gpt_audio_splitter_jobs"
JOBS_ROOT.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("audio-splitter")

ALLOWED_EXTENSIONS = {
    ".mp3", ".m4a", ".wav", ".webm", ".ogg", ".opus", ".aac",
    ".flac", ".mp4", ".mpeg", ".mpga", ".mov",
}

app = FastAPI(title="GPT Audio Splitter", version="2.0.0")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


class StartUploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=300)
    size: int = Field(gt=0)
    split_count: int
    access_key: str = ""


class FinishUploadRequest(BaseModel):
    job_id: str
    upload_token: str


def safe_stem(filename: str | None) -> str:
    raw_stem = Path(filename or "audio").stem
    cleaned = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", raw_stem).strip("._-")
    return cleaned[:80] or "audio"


def validate_access_key(value: str) -> None:
    if ACCESS_KEY and value != ACCESS_KEY:
        raise HTTPException(status_code=401, detail="접속 암호가 올바르지 않습니다.")


def cleanup_directory(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def cleanup_stale_jobs() -> None:
    now = time.time()
    for path in JOBS_ROOT.iterdir():
        try:
            if path.is_dir() and now - path.stat().st_mtime > JOB_MAX_AGE_SECONDS:
                cleanup_directory(path)
        except OSError:
            continue


def get_job_dir(job_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(status_code=400, detail="잘못된 작업 번호입니다.")
    job_dir = JOBS_ROOT / job_id
    if not job_dir.is_dir():
        raise HTTPException(status_code=404, detail="업로드 작업을 찾을 수 없습니다. 처음부터 다시 시도해 주세요.")
    return job_dir


def metadata_path(job_dir: Path) -> Path:
    return job_dir / "metadata.json"


def load_metadata(job_dir: Path) -> dict:
    try:
        return json.loads(metadata_path(job_dir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="업로드 작업 정보를 읽지 못했습니다.") from exc


def save_metadata(job_dir: Path, metadata: dict) -> None:
    metadata["updated_at"] = time.time()
    temp_path = job_dir / "metadata.tmp"
    temp_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    temp_path.replace(metadata_path(job_dir))
    os.utime(job_dir, None)


def verify_job_token(metadata: dict, token: str) -> None:
    if not token or not secrets.compare_digest(str(metadata.get("upload_token", "")), token):
        raise HTTPException(status_code=403, detail="업로드 인증 정보가 올바르지 않습니다.")


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
    result = run_command([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", str(input_path),
    ])
    try:
        payload = json.loads(result.stdout)
        duration = float(payload["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="오디오 재생 시간을 확인할 수 없습니다.") from exc
    if duration <= 0:
        raise HTTPException(status_code=422, detail="재생 시간이 0초인 파일은 처리할 수 없습니다.")
    return duration


def split_audio(input_path: Path, output_dir: Path, stem: str, split_count: int, duration: float) -> list[Path]:
    cut_points = [duration * i / split_count for i in range(1, split_count)]
    segment_times = ",".join(f"{point:.6f}" for point in cut_points)
    output_pattern = output_dir / f"{stem}_part_%02d_of_{split_count:02d}.mp3"
    run_command([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(input_path), "-map", "0:a:0", "-vn",
        "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "48k",
        "-f", "segment", "-segment_times", segment_times,
        "-segment_start_number", "1", "-reset_timestamps", "1",
        str(output_pattern),
    ])
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
    # MP3는 이미 압축된 형식이므로 ZIP_STORED가 더 빠르고 메모리 사용도 적습니다.
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for part in parts:
            archive.write(part, arcname=part.name)
        archive.writestr("사용안내.txt", readme)


def response_for_zip(job_dir: Path, metadata: dict) -> FileResponse:
    zip_path = job_dir / str(metadata["zip_name"])
    if not zip_path.is_file():
        raise HTTPException(status_code=404, detail="완성된 ZIP 파일을 찾지 못했습니다. 다시 처리해 주세요.")
    return FileResponse(
        path=zip_path,
        media_type="application/zip",
        filename=zip_path.name,
        headers={
            "X-Audio-Duration": str(metadata.get("duration", "")),
            "X-Split-Count": str(metadata["split_count"]),
        },
        background=BackgroundTask(cleanup_directory, job_dir),
    )


@app.on_event("startup")
def startup_cleanup() -> None:
    cleanup_stale_jobs()


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html = (APP_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    html = html.replace("{{MAX_UPLOAD_MB}}", str(MAX_UPLOAD_MB))
    html = html.replace("{{MAX_SPLITS}}", str(MAX_SPLITS))
    html = html.replace("{{REQUIRES_KEY}}", "true" if ACCESS_KEY else "false")
    return HTMLResponse(html)


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "version": "2.0.0"})


@app.post("/api/upload/start")
def start_upload(payload: StartUploadRequest) -> JSONResponse:
    cleanup_stale_jobs()
    validate_access_key(payload.access_key)
    if payload.split_count < 2 or payload.split_count > MAX_SPLITS:
        raise HTTPException(status_code=400, detail=f"분할 개수는 2~{MAX_SPLITS} 사이여야 합니다.")
    if payload.size > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"파일은 최대 {MAX_UPLOAD_MB}MB까지 업로드할 수 있습니다.")

    suffix = Path(payload.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise HTTPException(status_code=415, detail=f"지원하지 않는 형식입니다. 지원 형식: {allowed}")

    job_id = uuid.uuid4().hex
    upload_token = secrets.token_urlsafe(32)
    job_dir = JOBS_ROOT / job_id
    job_dir.mkdir(mode=0o700)
    metadata = {
        "job_id": job_id,
        "upload_token": upload_token,
        "filename": payload.filename,
        "suffix": suffix,
        "expected_size": payload.size,
        "received_size": 0,
        "next_chunk_index": 0,
        "split_count": payload.split_count,
        "status": "uploading",
        "created_at": time.time(),
    }
    save_metadata(job_dir, metadata)
    logger.info("upload started job=%s filename=%s size=%s", job_id, payload.filename, payload.size)
    return JSONResponse({
        "job_id": job_id,
        "upload_token": upload_token,
        "chunk_size": UPLOAD_CHUNK_BYTES,
    })


@app.post("/api/upload/{job_id}/chunk")
async def upload_chunk(
    job_id: str,
    request: Request,
    x_upload_token: str = Header(default=""),
    x_chunk_index: int = Header(default=-1),
) -> JSONResponse:
    job_dir = get_job_dir(job_id)
    metadata = load_metadata(job_dir)
    verify_job_token(metadata, x_upload_token)
    if metadata.get("status") != "uploading":
        raise HTTPException(status_code=409, detail="현재 업로드를 받을 수 없는 작업 상태입니다.")

    next_index = int(metadata["next_chunk_index"])
    # 서버가 청크를 받았지만 브라우저가 응답을 놓친 경우 같은 청크 재전송을 성공으로 처리합니다.
    if x_chunk_index < next_index:
        # 요청 본문을 끝까지 소비해야 HTTP 연결을 안전하게 재사용할 수 있습니다.
        discarded = 0
        async for data in request.stream():
            discarded += len(data)
            if discarded > UPLOAD_CHUNK_BYTES + 64 * 1024:
                raise HTTPException(status_code=413, detail="중복 업로드 조각이 허용 크기보다 큽니다.")
        return JSONResponse({
            "received_size": int(metadata["received_size"]),
            "next_chunk_index": next_index,
            "duplicate": True,
        })
    if x_chunk_index > next_index:
        raise HTTPException(
            status_code=409,
            detail=f"청크 순서가 맞지 않습니다. 서버가 기다리는 번호: {next_index}",
        )

    received_size = int(metadata["received_size"])
    expected_size = int(metadata["expected_size"])
    expected_chunk_size = min(UPLOAD_CHUNK_BYTES, expected_size - received_size)
    if expected_chunk_size <= 0:
        raise HTTPException(status_code=409, detail="이미 모든 업로드 조각을 받았습니다.")

    # 중간에 연결이 끊겨도 원본 파일이 손상되지 않도록 청크를 임시 파일에 완전히 받은 뒤 합칩니다.
    chunk_path = job_dir / f"chunk_{x_chunk_index:06d}.tmp"
    chunk_bytes = 0
    try:
        with chunk_path.open("wb") as chunk_file:
            async for data in request.stream():
                if not data:
                    continue
                chunk_bytes += len(data)
                if chunk_bytes > expected_chunk_size:
                    raise HTTPException(status_code=413, detail="업로드 조각이 허용 크기보다 큽니다.")
                chunk_file.write(data)

        if chunk_bytes != expected_chunk_size:
            raise HTTPException(
                status_code=400,
                detail=f"업로드 조각이 완전하지 않습니다. {chunk_bytes} / {expected_chunk_size} bytes",
            )

        input_path = job_dir / f"input{metadata['suffix']}"
        with input_path.open("ab") as destination, chunk_path.open("rb") as source:
            shutil.copyfileobj(source, destination, length=1024 * 1024)
    finally:
        chunk_path.unlink(missing_ok=True)

    new_total = received_size + chunk_bytes
    metadata["received_size"] = new_total
    metadata["next_chunk_index"] = x_chunk_index + 1
    save_metadata(job_dir, metadata)
    return JSONResponse({"received_size": new_total, "next_chunk_index": metadata["next_chunk_index"]})


@app.post("/api/upload/finish")
def finish_upload(payload: FinishUploadRequest) -> FileResponse:
    job_dir = get_job_dir(payload.job_id)
    metadata = load_metadata(job_dir)
    verify_job_token(metadata, payload.upload_token)

    if metadata.get("status") == "completed":
        return response_for_zip(job_dir, metadata)
    if metadata.get("status") == "processing":
        raise HTTPException(status_code=409, detail="이미 처리 중입니다. 잠시 후 다시 시도해 주세요.")
    if int(metadata["received_size"]) != int(metadata["expected_size"]):
        raise HTTPException(
            status_code=400,
            detail=f"업로드가 완전하지 않습니다. {metadata['received_size']} / {metadata['expected_size']} bytes",
        )

    metadata["status"] = "processing"
    save_metadata(job_dir, metadata)
    input_path = job_dir / f"input{metadata['suffix']}"
    output_dir = job_dir / "parts"
    output_dir.mkdir(exist_ok=True)

    try:
        logger.info("processing started job=%s", payload.job_id)
        duration = probe_duration(input_path)
        split_count = int(metadata["split_count"])
        if duration < split_count * 0.5:
            raise HTTPException(status_code=400, detail="분할 개수에 비해 오디오가 너무 짧습니다.")

        stem = safe_stem(str(metadata["filename"]))
        parts = split_audio(input_path, output_dir, stem, split_count, duration)

        # 원본은 분할 완료 직후 삭제하여 무료 서버의 임시 디스크 사용량을 낮춥니다.
        input_path.unlink(missing_ok=True)
        zip_name = f"{stem}_split_{split_count}.zip"
        zip_path = job_dir / zip_name
        create_zip(zip_path, parts, str(metadata["filename"]), duration)

        metadata["status"] = "completed"
        metadata["duration"] = f"{duration:.3f}"
        metadata["zip_name"] = zip_name
        save_metadata(job_dir, metadata)
        logger.info("processing completed job=%s duration=%.3f", payload.job_id, duration)
        return response_for_zip(job_dir, metadata)
    except HTTPException:
        metadata["status"] = "failed"
        save_metadata(job_dir, metadata)
        logger.exception("processing failed job=%s", payload.job_id)
        raise
    except Exception as exc:
        metadata["status"] = "failed"
        save_metadata(job_dir, metadata)
        logger.exception("unexpected processing error job=%s", payload.job_id)
        raise HTTPException(status_code=500, detail=f"예상하지 못한 오류가 발생했습니다: {exc}") from exc
