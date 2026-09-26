#!/usr/bin/env python3

import json
import os
import csv
import io
import subprocess
import signal
import threading
import urllib.parse
import urllib.request
import queue
import requests
from bs4 import BeautifulSoup
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "0.0.0.0"
PORT = 8080

BASE_DIR = Path("/opt/music-downloader")
DOWNLOAD_DIR = Path("/opt/docker/navidrome/music")
MOBILE_DOWNLOAD_DIR = BASE_DIR / "mobile-downloads"
TAG_SCRIPT = BASE_DIR / "tag-music.py"

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
MOBILE_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

downloads = {}
download_lock = threading.Lock()

download_queue = queue.Queue()
active_processes = {}

# Control real de descargas:
# paused    -> descarga pausada mediante SIGSTOP
# cancelled -> descarga cancelada
download_controls = {}
download_control_lock = threading.Lock()
download_pause_started = {}


def get_download_control(job):
    with download_control_lock:
        return download_controls.get(job, "")


def set_download_control(job, state):
    with download_control_lock:

        if state:

            download_controls[job] = state

            if state == "paused":
                download_pause_started[job] = time.time()
            else:
                download_pause_started.pop(job, None)

        else:

            download_controls.pop(job, None)
            download_pause_started.pop(job, None)


def clear_download_control(job):
    with download_control_lock:
        download_controls.pop(job, None)
        download_pause_started.pop(job, None)

HISTORY_FILE = BASE_DIR / "download-history.json"
MOBILE_DOWNLOADS_STATE_FILE = BASE_DIR / "mobile-downloads.json"

history = []


def save_mobile_downloads_state():

    try:

        mobile_items = {}

        for job, item in downloads.items():

            if item.get("type") == "mobile":

                mobile_items[job] = {
                    "job": job,
                    "status": item.get(
                        "status",
                        "unknown"
                    ),
                    "message": item.get(
                        "message",
                        ""
                    ),
                    "progress": item.get(
                        "progress",
                        0
                    ),
                    "title": item.get(
                        "title",
                        ""
                    ),
                    "id": item.get(
                        "id",
                        ""
                    ),
                    "type": "mobile",
                    "file": item.get(
                        "file",
                        ""
                    ),
                    "filename": item.get(
                        "filename",
                        ""
                    ),
                    "size": item.get(
                        "size",
                        0
                    )
                }

        temp_file = MOBILE_DOWNLOADS_STATE_FILE.with_suffix(
            ".json.tmp"
        )

        temp_file.write_text(
            json.dumps(
                mobile_items,
                ensure_ascii=False,
                indent=2
            )
        )

        temp_file.replace(
            MOBILE_DOWNLOADS_STATE_FILE
        )

    except Exception as error:

        print(
            "ERROR guardando estado de descargas móviles:",
            repr(error)
        )


def load_mobile_downloads_state():

    try:

        if not MOBILE_DOWNLOADS_STATE_FILE.exists():
            return

        loaded = json.loads(
            MOBILE_DOWNLOADS_STATE_FILE.read_text()
        )

        if not isinstance(loaded, dict):
            return

        for job, item in loaded.items():

            if not isinstance(item, dict):
                continue

            if item.get("type") != "mobile":
                continue

            downloads[job] = item

    except Exception as error:

        print(
            "ERROR cargando estado de descargas móviles:",
            repr(error)
        )


load_mobile_downloads_state()

try:
    if HISTORY_FILE.exists():
        loaded_history = json.loads(
            HISTORY_FILE.read_text()
        )

        if isinstance(loaded_history, list):
            history = loaded_history

except Exception:
    history = []


def save_history():

    try:
        HISTORY_FILE.write_text(
            json.dumps(
                history[-100:],
                ensure_ascii=False,
                indent=2
            )
        )
    except Exception:
        pass


def add_history(job, video_id, title=""):

    item = downloads.get(
        job,
        {}
    )

    download_type = item.get(
        "type",
        "navidrome"
    )

    history_type = (
        "mobile"
        if download_type == "mobile"
        else "navidrome"
    )

    album_group = item.get(
        "album_group"
    )

    # Los álbumes tienen varios jobs independientes,
    # pero aparecen como una sola entrada en el historial.
    if album_group:

        group_items = [
            (group_job, group_item)
            for group_job, group_item in downloads.items()
            if group_item.get("album_group") == album_group
        ]

        if not group_items:
            return

        terminal_states = {
            "done",
            "cancelled",
            "error"
        }

        # Esperar hasta que todos los temas del álbum
        # hayan llegado a un estado final.
        if any(
                group_item.get("status") not in terminal_states
                for _, group_item in group_items
        ):
            return

        # Evitar duplicados.
        history[:] = [
            entry
            for entry in history
            if entry.get("album_group") != album_group
        ]

        ordered_items = sorted(
            group_items,
            key=lambda pair: (
                pair[1].get("album_track_index", 0),
                pair[0]
            )
        )

        tracks = []

        for group_job, group_item in ordered_items:
            tracks.append({
                "job": group_job,
                "id": group_item.get(
                    "id",
                    ""
                ),
                "title": group_item.get(
                    "title",
                    group_item.get(
                        "id",
                        ""
                    )
                ),
                "track_index": group_item.get(
                    "album_track_index",
                    0
                ),
                "status": group_item.get(
                    "status",
                    "error"
                ),
                "progress": group_item.get(
                    "progress",
                    0
                )
            })

        if all(
                track["status"] == "done"
                for track in tracks
        ):
            group_status = "done"

        elif any(
                track["status"] == "error"
                for track in tracks
        ):
            group_status = "error"

        else:
            group_status = "cancelled"

        album_title = (
            item.get("album_title")
            or "Álbum"
        )

        history.append({
            "job": album_group,
            "id": album_group,
            "title": album_title,
            "time": time.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "status": group_status,
            "type": history_type,
            "album_group": album_group,
            "album_title": album_title,
            "album_track_total": item.get(
                "album_track_total",
                len(tracks)
            ),
            "tracks": tracks
        })

        save_history()
        return

    # Las canciones individuales mantienen
    # exactamente el historial anterior.
    history.append({
        "job": job,
        "id": video_id,
        "title": title or video_id,
        "time": time.strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "status": item.get(
            "status",
            "done"
        ),
        "type": history_type
    })

    save_history()


def pause_download(job):

    process = active_processes.get(job)

    if process is None:
        return False, "La descarga todavía no está activa."

    if process.poll() is not None:
        return False, "La descarga ya ha terminado."

    try:

        process.send_signal(signal.SIGSTOP)

        set_download_control(job, "paused")

        if job in downloads:

            downloads[job]["status"] = "paused"
            downloads[job]["message"] = "Descarga pausada."

            if downloads[job].get("type") == "mobile":
                save_mobile_downloads_state()

        return True, "Descarga pausada."

    except Exception as error:

        return False, str(error)


def resume_download(job):

    process = active_processes.get(job)

    if process is None:

        # Puede tratarse de un trabajo que todavía está
        # esperando en la cola.
        if get_download_control(job) == "paused":

            set_download_control(job, "")

            if job in downloads:

                downloads[job]["status"] = "queued"
                downloads[job]["message"] = "En cola..."

                if downloads[job].get("type") == "mobile":
                    save_mobile_downloads_state()

            return True, "Descarga reanudada en cola."

        return False, "La descarga no está activa."


    if process.poll() is not None:

        return False, "La descarga ya ha terminado."


    try:

        process.send_signal(signal.SIGCONT)

        set_download_control(job, "")

        if job in downloads:

            downloads[job]["status"] = "running"
            downloads[job]["message"] = "Descargando..."

            if downloads[job].get("type") == "mobile":
                save_mobile_downloads_state()

        return True, "Descarga reanudada."

    except Exception as error:

        return False, str(error)


def cancel_download(job):

    process = active_processes.get(job)

    # Cancelación de un trabajo que todavía está en cola.
    if process is None:

        if job in downloads and downloads[job].get("status") in (
            "queued",
            "paused"
        ):

            set_download_control(job, "cancelled")

            item = downloads[job]

            item["status"] = "cancelled"
            item["message"] = "Descarga cancelada."

            add_history(
                job,
                item.get("id", ""),
                item.get("title", "")
            )

            if item.get("type") == "mobile":
                save_mobile_downloads_state()

            return True, "Descarga cancelada."

        return False, "La descarga no está activa."


    try:

        # Si estaba pausado, SIGTERM por sí solo no puede
        # despertarlo. Primero lo reanudamos y después
        # terminamos el proceso.
        if get_download_control(job) == "paused":

            try:
                process.send_signal(signal.SIGCONT)
            except Exception:
                pass

        set_download_control(job, "cancelled")

        process.terminate()

        if job in downloads:

            item = downloads[job]

            item["status"] = "cancelled"
            item["message"] = "Descarga cancelada."

            if not item.get("type"):
                item["type"] = "navidrome"

            if item.get("type") == "mobile":
                save_mobile_downloads_state()

        return True, "Descarga cancelada."

    except Exception as error:

        return False, str(error)


def queue_worker():

    while True:

        item = download_queue.get()

        try:

            # Descarga móvil.
            #
            # Formato antiguo:
            # (job, video_id, title, "mobile")
            #
            # Formato con álbum:
            # (
            #     job,
            #     video_id,
            #     title,
            #     "mobile",
            #     album_group,
            #     album_title,
            #     album_track_index,
            #     album_track_total
            # )

            if len(item) >= 4 and item[3] == "mobile":

                job = item[0]
                video_id = item[1]
                title = item[2]

                album_group = (
                    item[4]
                    if len(item) > 4
                    else None
                )

                album_title = (
                    item[5]
                    if len(item) > 5
                    else None
                )

                album_track_index = (
                    item[6]
                    if len(item) > 6
                    else 0
                )

                album_track_total = (
                    item[7]
                    if len(item) > 7
                    else 0
                )

                format_name = (
                    item[8]
                    if len(item) > 8
                    else "mp3"
                )

                if get_download_control(job) == "paused":

                    downloads[job]["status"] = "paused"
                    downloads[job]["message"] = (
                        "En pausa. Esperando reanudación."
                    )

                    save_mobile_downloads_state()

                    download_queue.put(item)

                    time.sleep(0.5)

                    continue

                do_download_mobile(
                    job,
                    video_id,
                    title,
                    album_group,
                    album_title,
                    album_track_index,
                    album_track_total,
                    format_name
                )

            else:

                # Navidrome.
                #
                # Formato antiguo:
                # (job, video_id, title)
                #
                # Formato con álbum:
                # (
                #     job,
                #     video_id,
                #     title,
                #     album_group,
                #     album_title,
                #     album_track_index,
                #     album_track_total
                # )

                job = item[0]
                video_id = item[1]
                title = item[2]

                album_group = (
                    item[3]
                    if len(item) > 3
                    else None
                )

                album_title = (
                    item[4]
                    if len(item) > 4
                    else None
                )

                album_track_index = (
                    item[5]
                    if len(item) > 5
                    else 0
                )

                album_track_total = (
                    item[6]
                    if len(item) > 6
                    else 0
                )

                if get_download_control(job) == "paused":

                    downloads[job]["status"] = "paused"
                    downloads[job]["message"] = (
                        "En pausa. Esperando reanudación."
                    )

                    download_queue.put(item)

                    time.sleep(0.5)

                    continue

                do_download(
                    job,
                    video_id,
                    title,
                    album_group,
                    album_title,
                    album_track_index,
                    album_track_total
                )

        finally:

            download_queue.task_done()


queue_thread = threading.Thread(
    target=queue_worker,
    daemon=True
)

queue_thread.start()


HTML = r"""
<!DOCTYPE html>
<html lang="es">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1.0"
>

<title>Music Downloader</title>

<style>

* {
    box-sizing: border-box;
}

:root {
    --bg: #080b10;
    --panel: rgba(18, 22, 30, .94);
    --panel2: rgba(25, 30, 40, .96);
    --border: rgba(255,255,255,.09);
    --text: #f4f7fb;
    --muted: #929aaa;
    --accent: #7c5cff;
    --accent2: #9b82ff;
    --green: #35c978;
    --red: #ff5d68;
    --shadow: 0 18px 55px rgba(0,0,0,.35);
}

body {
    margin: 0;
    min-height: 100vh;
    color: var(--text);
    font-family:
        Inter,
        ui-sans-serif,
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;

    background:
        radial-gradient(
            circle at 20% 0%,
            rgba(124,92,255,.18),
            transparent 32%
        ),
        radial-gradient(
            circle at 90% 10%,
            rgba(35,134,255,.12),
            transparent 28%
        ),
        linear-gradient(
            180deg,
            #090c12 0%,
            #07090d 100%
        );
}

button,
input {
    font: inherit;
}

button {
    -webkit-tap-highlight-color: transparent;
}

.header {
    position: sticky;
    top: 0;
    z-index: 50;

    border-bottom: 1px solid var(--border);

    background:
        rgba(8,11,16,.82);

    backdrop-filter: blur(18px);
}

.header-inner {
    max-width: 1180px;
    margin: auto;
    padding: 18px 22px;

    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 20px;
}

.logo {
    display: flex;
    align-items: center;
    gap: 11px;

    font-size: 21px;
    font-weight: 800;
    letter-spacing: -.4px;
}

.logo-icon {
    width: 40px;
    height: 40px;

    display: grid;
    place-items: center;

    border-radius: 12px;

    background:
        linear-gradient(
            135deg,
            var(--accent),
            #4b8cff
        );

    box-shadow:
        0 8px 25px rgba(124,92,255,.25);
}

.subtitle {
    color: var(--muted);
    font-size: 13px;
}

.container {
    max-width: 1180px;
    margin: auto;
    padding: 34px 22px 70px;
}

.hero {
    margin-bottom: 28px;
}

.hero h1 {
    margin: 0 0 8px;

    font-size: clamp(30px, 5vw, 48px);
    line-height: 1.05;
    letter-spacing: -1.7px;
}

.hero p {
    margin: 0;
    color: var(--muted);
    font-size: 15px;
}

.search {
    display: flex;
    gap: 10px;

    padding: 9px;

    border: 1px solid var(--border);
    border-radius: 18px;

    background:
        rgba(18,22,30,.9);

    box-shadow: var(--shadow);

    margin-bottom: 28px;
}

.search input {
    min-width: 0;
    flex: 1;

    border: 0;
    outline: 0;

    padding: 15px 17px;

    background: transparent;
    color: var(--text);

    font-size: 17px;
}

.search input::placeholder {
    color: #737b8c;
}

.search button {
    border: 0;
    border-radius: 13px;

    padding: 0 24px;

    color: white;
    background:
        linear-gradient(
            135deg,
            var(--accent),
            #5d7cff
        );

    font-weight: 800;
    cursor: pointer;

    transition:
        transform .18s,
        filter .18s;
}

.search button:hover {
    filter: brightness(1.12);
    transform: translateY(-1px);
}

.search button:active {
    transform: translateY(0);
}

.results {
    display: flex;
    flex-direction: column;
    gap: 13px;
}

.result {
    display: grid;

    grid-template-columns: 190px minmax(0, 1fr) auto;

    gap: 18px;

    align-items: center;

    padding: 15px;

    border: 1px solid var(--border);
    border-radius: 18px;

    background:
        linear-gradient(
            135deg,
            rgba(22,27,36,.96),
            rgba(15,19,26,.96)
        );

    box-shadow:
        0 8px 30px rgba(0,0,0,.18);

    transition:
        border-color .2s,
        transform .2s,
        background .2s;
}

.result:hover {
    border-color: rgba(124,92,255,.35);

    transform: translateY(-1px);

    background:
        linear-gradient(
            135deg,
            rgba(27,32,43,.98),
            rgba(17,21,29,.98)
        );
}

.thumbnail-wrap {
    position: relative;

    width: 190px;
    height: 108px;

    overflow: hidden;

    border-radius: 13px;

    background: #11151d;
}

.thumbnail {
    width: 100%;
    height: 100%;

    display: block;

    object-fit: cover;
}

.thumbnail-wrap::after {
    content: "";

    position: absolute;
    inset: 0;

    background:
        linear-gradient(
            180deg,
            transparent 45%,
            rgba(0,0,0,.3)
        );

    pointer-events: none;
}

.info {
    min-width: 0;
}

.title {
    margin-bottom: 8px;

    color: var(--text);

    font-size: 17px;
    line-height: 1.35;
    font-weight: 800;

    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;

    overflow: hidden;
}

.meta {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 7px;

    color: var(--muted);

    font-size: 13px;
}

.meta-pill {
    display: inline-flex;
    align-items: center;

    padding: 5px 9px;

    border-radius: 8px;

    background: rgba(255,255,255,.055);
}

.channel {
    color: #b5bdcc;
}

.status {
    min-height: 20px;

    margin-top: 9px;

    color: var(--muted);
    font-size: 13px;
}

.success {
    color: var(--green);
    font-weight: 800;
}

.error {
    color: var(--red);
}

.buttons {
    display: flex;
    align-items: center;
    justify-content: flex-end;

    gap: 8px;

    flex-wrap: wrap;
}

.btn {
    border: 0;
    border-radius: 11px;

    padding: 11px 15px;

    cursor: pointer;

    color: white;

    font-size: 13px;
    font-weight: 800;

    white-space: nowrap;

    transition:
        transform .16s,
        filter .16s,
        opacity .16s;
}

.btn:hover {
    filter: brightness(1.12);
    transform: translateY(-1px);
}

.btn:active {
    transform: translateY(0);
}

.btn:disabled {
    opacity: .5;
    cursor: wait;
    transform: none;
}

.preview {
    background: #272d39;
    border: 1px solid rgba(255,255,255,.07);
}

.preview.active {
    background: #463a75;
}

.download {
    background:
        linear-gradient(
            135deg,
            #7c5cff,
            #5e7cff
        );
}

.cancel {
    background: rgba(255,80,95,.14);
    color: #ff737c;

    border: 1px solid rgba(255,80,95,.22);
}

.player {
    grid-column: 1 / -1;

    padding: 14px 16px;

    border-radius: 14px;

    background:
        rgba(8,11,16,.7);

    border: 1px solid var(--border);
}

.player-info {
    margin-bottom: 9px;

    color: var(--muted);
    font-size: 12px;
}

.player audio {
    display: block;

    width: 100%;
    height: 42px;
}

.card {
    margin-top: 25px;

    padding: 20px;

    border: 1px solid var(--border);
    border-radius: 18px;

    background:
        var(--panel);

    box-shadow:
        0 12px 40px rgba(0,0,0,.18);
}

.card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;

    gap: 15px;

    margin-bottom: 15px;
}

.card-title {
    font-weight: 850;
    font-size: 16px;
}

.card-subtitle {
    color: var(--muted);
    font-size: 12px;
}

.library {
    display: flex;
    align-items: center;
    gap: 14px;
}

.library-icon {
    width: 46px;
    height: 46px;

    flex: 0 0 auto;

    display: grid;
    place-items: center;

    border-radius: 13px;

    background: rgba(53,201,120,.1);

    font-size: 21px;
}

.library strong {
    display: block;
    margin-bottom: 4px;
}

.library span {
    color: var(--muted);
    font-size: 13px;
}

.queue-item {
    padding: 14px 0;

    border-bottom:
        1px solid var(--border);
}

.queue-item:last-child {
    border-bottom: 0;
}

.queue-top {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;

    gap: 15px;
}

.queue-title {
    min-width: 0;

    font-size: 14px;
    font-weight: 750;

    line-height: 1.4;
}

.queue-status {
    margin-top: 5px;

    color: var(--muted);
    font-size: 12px;
}

.queue-percent {
    color: #bcaeff;

    font-size: 13px;
    font-weight: 850;
}

.progress-bar {
    width: 100%;
    height: 7px;

    margin-top: 10px;

    overflow: hidden;

    border-radius: 99px;

    background: #202631;
}

.progress-fill {
    height: 100%;
    width: 0%;

    border-radius: inherit;

    background:
        linear-gradient(
            90deg,
            var(--accent),
            #5f91ff
        );

    transition: width .35s ease;
}


.history-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    margin-bottom: 8px;
}

.history-clear-button {
    padding: 6px 10px;
    border: 1px solid rgba(239,68,68,.45);
    border-radius: 8px;
    background: rgba(239,68,68,.12);
    color: inherit;
    font-size: 11px;
    font-weight: 700;
    cursor: pointer;
}

.history-clear-button:hover {
    background: rgba(239,68,68,.24);
    border-color: rgba(239,68,68,.70);
}

.history-title {
    display: flex;
    align-items: center;
    gap: 8px;

    margin-top: 20px;
    margin-bottom: 8px;

    padding-top: 17px;

    border-top: 1px solid var(--border);

    font-size: 13px;
    font-weight: 850;
}

.history-item {
    display: flex;
    align-items: center;

    gap: 9px;

    padding: 8px 0;

    color: var(--muted);

    font-size: 12px;
}

.history-status {
    flex: 0 0 auto;
}

.pagination {
    display: flex;

    align-items: center;
    justify-content: center;

    gap: 14px;

    margin: 28px 0 4px;
}

.pagination button {
    border: 1px solid var(--border);
    border-radius: 10px;

    padding: 10px 15px;

    background: #171c25;
    color: white;

    cursor: pointer;

    font-weight: 750;
}

.pagination button:hover:not(:disabled) {
    background: #232936;
}

.pagination button:disabled {
    opacity: .3;
    cursor: default;
}

.page {
    color: var(--muted);

    font-size: 13px;
    font-weight: 700;
}

.notice {
    position: fixed;

    left: 50%;
    bottom: 25px;

    z-index: 9999;

    max-width: calc(100vw - 30px);

    padding: 13px 19px;

    border: 1px solid rgba(53,201,120,.25);
    border-radius: 13px;

    background:
        rgba(16,25,21,.96);

    color: #7be4a6;

    box-shadow:
        0 15px 45px rgba(0,0,0,.45);

    font-size: 13px;
    font-weight: 800;

    transform:
        translate(-50%, 120px);

    transition:
        transform .3s ease;
}

.notice.show {
    transform:
        translate(-50%, 0);
}

.loading {
    display: flex;
    align-items: center;
    justify-content: center;

    gap: 10px;

    padding: 45px 20px;

    color: var(--muted);
}

.spinner {
    width: 18px;
    height: 18px;

    border: 2px solid #313847;
    border-top-color: var(--accent);

    border-radius: 50%;

    animation:
        spin .7s linear infinite;
}

@keyframes spin {
    to {
        transform: rotate(360deg);
    }
}

.empty {
    text-align: center;

    padding: 45px 20px;

    color: var(--muted);
}

@media(max-width:850px) {

    .result {
        grid-template-columns: 135px minmax(0, 1fr);
    }

    .thumbnail-wrap {
        width: 135px;
        height: 82px;
    }

    .buttons {
        grid-column: 1 / -1;

        justify-content: stretch;
    }

    .buttons .btn {
        flex: 1;
    }
}

@media(max-width:600px) {

    .header-inner {
        padding: 14px 15px;
    }

    .subtitle {
        display: none;
    }

    .container {
        padding: 25px 13px 55px;
    }

    .hero h1 {
        font-size: 31px;
    }

    .search {
        flex-direction: column;

        padding: 7px;

        border-radius: 15px;
    }

    .search input {
        padding: 13px 12px;
    }

    .search button {
        min-height: 47px;
    }

    .result {
        grid-template-columns: 105px minmax(0, 1fr);

        gap: 12px;

        padding: 11px;

        border-radius: 15px;
    }

    .thumbnail-wrap {
        width: 105px;
        height: 66px;

        border-radius: 10px;
    }

    .title {
        font-size: 14px;
        margin-bottom: 5px;
    }

    .meta {
        font-size: 11px;
    }

    .meta-pill {
        padding: 4px 7px;
    }

    .status {
        font-size: 11px;
    }

    .buttons {
        display: grid;

        grid-template-columns: 1fr 1fr;
    }

    .btn {
        width: 100%;
        padding: 11px 8px;
    }

    .cancel {
        grid-column: 1 / -1;
    }

    .card {
        padding: 16px;

        border-radius: 15px;
    }
}



/* =========================================================
   NUEVA INTERFAZ MUSIC DOWNLOADER
   Solo UI - no modifica backend
========================================================= */

/* Resultados cuadrados tipo biblioteca */
.results {
    display: grid !important;

    grid-template-columns:
        repeat(
            auto-fill,
            minmax(210px, 1fr)
        ) !important;

    gap: 15px !important;

    width: 100%;
}

.result {
    display: flex !important;
    flex-direction: column !important;
    align-items: stretch !important;
    gap: 0 !important;

    padding: 12px !important;

    border-radius: 18px !important;

    background:
        linear-gradient(
            145deg,
            rgba(25,30,40,.98),
            rgba(14,18,25,.98)
        ) !important;

    box-shadow:
        0 12px 35px rgba(0,0,0,.22) !important;
}

.result:hover {
    transform: translateY(-3px) !important;
    border-color: rgba(124,92,255,.45) !important;
}

/* Portada 1:1 */
.thumbnail-wrap {
    width: 100% !important;
    height: auto !important;
    aspect-ratio: 1 / 1 !important;

    border-radius: 14px !important;

    margin-bottom: 13px !important;

    box-shadow:
        0 10px 25px rgba(0,0,0,.25);
}

.thumbnail {
    width: 100% !important;
    height: 100% !important;
    object-fit: cover !important;
}

/* Información */
.info {
    padding: 0 3px !important;
}

.title {
    font-size: 15px !important;
    line-height: 1.35 !important;
    min-height: 40px;
    margin-bottom: 8px !important;
}

.meta {
    gap: 6px !important;
}

.channel {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 100%;
}

.status {
    min-height: 19px !important;
    margin: 9px 3px 3px !important;
    font-size: 12px !important;
}

/* Botones */
.buttons {
    display: grid !important;
    grid-template-columns: 1fr 1fr !important;

    gap: 8px !important;

    margin-top: 12px !important;
}

.btn {
    min-height: 40px !important;

    padding: 9px 8px !important;

    border-radius: 11px !important;

    font-size: 12px !important;
    letter-spacing: .1px;
}

.preview {
    background:
        rgba(255,255,255,.065) !important;

    border: 1px solid rgba(255,255,255,.09) !important;
}

.preview:hover {
    background:
        rgba(255,255,255,.11) !important;
}

.download {
    background:
        linear-gradient(
            135deg,
            #7657f5,
            #557cff
        ) !important;

    box-shadow:
        0 6px 18px rgba(100,85,240,.20);
}

.cancel {
    grid-column: 1 / -1;

    background:
        rgba(255,80,95,.10) !important;

    border: 1px solid rgba(255,80,95,.22) !important;
}

/* ---------------------------------------------------------
   COLA DE DESCARGAS
--------------------------------------------------------- */

.queue-list-modern {
    display: flex;
    flex-direction: column;
    gap: 10px;
}

.queue-card {
    padding: 14px;

    border: 1px solid var(--border);

    border-radius: 14px;

    background:
        linear-gradient(
            145deg,
            rgba(27,32,42,.85),
            rgba(16,20,28,.9)
        );
}

.queue-card-top {
    display: flex;
    align-items: flex-start;
    gap: 12px;
}

.queue-card-main {
    min-width: 0;
    flex: 1;
}

.queue-card-title {
    font-size: 14px;
    font-weight: 800;
    line-height: 1.35;

    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
}

.queue-card-status {
    margin-top: 5px;

    color: var(--muted);

    font-size: 12px;
}

.queue-badge {
    flex: 0 0 auto;

    padding: 5px 9px;

    border-radius: 999px;

    background: rgba(124,92,255,.13);

    color: #bcaeff;

    font-size: 10px;
    font-weight: 850;

    text-transform: uppercase;
    letter-spacing: .5px;
}

.queue-badge.running {
    background: rgba(124,92,255,.16);
    color: #c6baff;
}

.queue-badge.queued {
    background: rgba(255,255,255,.06);
    color: #aeb6c5;
}

.queue-badge.done {
    background: rgba(53,201,120,.11);
    color: #63dc96;
}

.queue-badge.error,
.queue-badge.cancelled {
    background: rgba(255,80,95,.10);
    color: #ff747d;
}

.queue-progress-row {
    display: flex;
    align-items: center;
    gap: 10px;

    margin-top: 12px;
}

.queue-progress-row .progress-bar {
    flex: 1;
    margin-top: 0;
    height: 8px;
}

.queue-progress-number {
    min-width: 38px;

    text-align: right;

    color: #c4b7ff;

    font-size: 12px;
    font-weight: 850;
}

.queue-card-actions {
    display: flex;
    justify-content: flex-end;

    margin-top: 11px;
}

.queue-cancel {
    border: 1px solid rgba(255,80,95,.20);

    border-radius: 9px;

    padding: 7px 11px;

    background: rgba(255,80,95,.08);

    color: #ff747d;

    font-size: 11px;
    font-weight: 800;

    cursor: pointer;
}

.queue-cancel:hover {
    background: rgba(255,80,95,.14);
}

.queue-empty {
    padding: 20px;

    text-align: center;

    color: var(--muted);

    font-size: 13px;
}

/* Historial */
.history-item {
    padding: 10px 0 !important;
}

.history-status {
    min-width: 18px;
}

/* ---------------------------------------------------------
   RESPONSIVE
--------------------------------------------------------- */

@media(max-width:1050px) {
    .results {
        grid-template-columns: repeat(3, minmax(0, 1fr)) !important;
    }
}

@media(max-width:760px) {
    .results {
        grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
        gap: 12px !important;
    }

    .result {
        padding: 10px !important;
        border-radius: 15px !important;
    }

    .thumbnail-wrap {
        border-radius: 11px !important;
        margin-bottom: 10px !important;
    }

    .title {
        font-size: 13px !important;
        min-height: 35px;
    }

    .meta {
        font-size: 10px !important;
    }

    .status {
        font-size: 10px !important;
    }

    .btn {
        min-height: 38px !important;
        font-size: 11px !important;
    }
}

@media(max-width:430px) {
    .results {
        grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
        gap: 9px !important;
    }

    .result {
        padding: 8px !important;
        border-radius: 13px !important;
    }

    .title {
        font-size: 12px !important;
        min-height: 33px;
    }

    .meta-pill {
        padding: 3px 6px !important;
    }

    .buttons {
        gap: 5px !important;
    }

    .btn {
        padding: 9px 4px !important;
        min-height: 36px !important;
        font-size: 10px !important;
    }
}



/* =========================================================
   RESULTADOS: MAXIMO APROVECHAMIENTO DE PANTALLA
========================================================= */

.results {
    align-items: start !important;
}

.result {
    width: 100%;
    box-sizing: border-box;
}

.thumbnail-wrap {
    width: 100% !important;
    aspect-ratio: 1 / 1 !important;
    height: auto !important;
}

.info {
    min-width: 0;
}

.buttons {
    width: 100%;
}

.buttons .btn {
    min-width: 0;
}

/* Pantallas grandes */
@media (min-width: 1400px) {
    .results {
        grid-template-columns:
            repeat(
                5,
                minmax(0, 1fr)
            ) !important;

        gap: 16px !important;
    }
}

/* Pantallas normales */
@media (min-width: 1000px) and (max-width: 1399px) {
    .results {
        grid-template-columns:
            repeat(
                4,
                minmax(0, 1fr)
            ) !important;
    }
}

/* Tablet */
@media (min-width: 761px) and (max-width: 999px) {
    .results {
        grid-template-columns:
            repeat(
                3,
                minmax(0, 1fr)
            ) !important;
    }
}

/* Móvil */
@media (max-width: 760px) {
    .results {
        grid-template-columns:
            repeat(
                2,
                minmax(0, 1fr)
            ) !important;

        gap: 10px !important;
    }
}

/* Móvil pequeño */
@media (max-width: 430px) {
    .results {
        grid-template-columns:
            repeat(
                2,
                minmax(0, 1fr)
            ) !important;

        gap: 8px !important;
    }
}

/* Ocultar paginación: todo está en una sola página */
#pagination {
    display: none !important;
}


/* =========================================================
   LISTA DE ALBUMES
========================================================= */

.album-list {
    display: flex;
    flex-direction: column;
    gap: 7px;
    margin-top: 16px;
}

.album-select {
    width: 100%;
    min-height: 52px;
    display: flex;
    align-items: center;
    gap: 12px;

    padding: 10px 13px;

    border: 1px solid rgba(255,255,255,.08);
    border-radius: 10px;

    background: rgba(255,255,255,.035);
    color: inherit;

    text-align: left;
    cursor: pointer;

    box-sizing: border-box;

    transition:
        background .15s ease,
        border-color .15s ease,
        transform .15s ease;
}

.album-select:hover {
    background: rgba(255,255,255,.085);
    border-color: rgba(255,255,255,.14);
    transform: translateX(2px);
}

.album-select-number {
    width: 30px;
    min-width: 30px;
    height: 30px;

    display: flex;
    align-items: center;
    justify-content: center;

    border-radius: 8px;
    background: rgba(124,58,237,.18);

    font-size: 12px;
    font-weight: 600;
    opacity: .8;
}

.album-select-info {
    flex: 1;
    min-width: 0;

    display: flex;
    flex-direction: column;
    justify-content: center;

    gap: 3px;
}

.album-select-info strong {
    display: block;

    font-size: 15px;
    font-weight: 600;
    line-height: 1.25;

    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.album-select-info small {
    display: block;

    font-size: 11px;
    line-height: 1.2;

    opacity: .48;
}

.album-select-arrow {
    width: 22px;
    min-width: 22px;

    text-align: center;

    font-size: 24px;
    line-height: 1;

    opacity: .35;
}

.album-select:hover .album-select-arrow {
    opacity: .75;
}


/* =========================================================
   MOVIL
========================================================= */

@media (max-width: 700px) {

    .album-list {
        gap: 6px;
        margin-top: 13px;
    }

    .album-select {
        min-height: 50px;
        padding: 9px 10px;
        gap: 10px;
    }

    .album-select-number {
        width: 28px;
        min-width: 28px;
        height: 28px;
        font-size: 11px;
    }

    .album-select-info strong {
        font-size: 13px;
    }

    .album-select-info small {
        font-size: 10px;
    }

    .album-select-arrow {
        font-size: 21px;
    }
}


/* =========================================================
   PROGRESO DE DESCARGA EN ALBUM
========================================================= */

.album-track-progress {
    width: 100%;
    align-items: center;
    gap: 7px;
    margin-top: 6px;
}

.album-track-progress-bar {
    flex: 1;
    height: 5px;
    overflow: hidden;
    border-radius: 99px;
    background: rgba(255,255,255,.10);
}

.album-track-progress-fill {
    height: 100%;
    width: 0%;
    border-radius: 99px;
    background: rgba(124,58,237,.95);
    transition: width .4s ease;
}

.album-track-progress-text {
    min-width: 32px;
    text-align: right;
    font-size: 10px;
    opacity: .65;
}

@media (max-width: 700px) {

    .album-track-progress {
        margin-top: 5px;
    }

    .album-track-progress-bar {
        height: 4px;
    }
}


/* =========================================================
   ESTADO COMPLETADO DEL ALBUM
========================================================= */

.album-track-progress-fill.album-progress-done {
    background: #22c55e;
}

.album-track-progress-text {
    transition: color .2s ease;
}

.album-track-status.album-status-done {
    color: #22c55e !important;
    opacity: 1 !important;
    font-weight: 600;
}


/* =========================================================
   PROGRESO DENTRO DE LAS TARJETAS DE BUSCAR CANCIONES
   ========================================================= */

.result-progress {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 7px;
    margin-top: 7px;
}

.result-progress-bar {
    flex: 1;
    height: 5px;
    overflow: hidden;
    border-radius: 99px;
    background: rgba(255,255,255,.10);
}

.result-progress-fill {
    height: 100%;
    width: 0%;
    border-radius: 99px;
    background: rgba(124,58,237,.95);
    transition: width .4s ease;
}

.result-progress-fill.result-progress-done {
    background: #22c55e;
}

.result-progress-text {
    min-width: 32px;
    text-align: right;
    font-size: 10px;
    opacity: .65;
}



/* =========================================================
   REPRODUCTOR DE ÁLBUM MODERNO
========================================================= */

.album-player {
    position: fixed !important;

    left: 18px;
    right: 18px;
    bottom: 18px;

    z-index: 9999;

    display: grid;
    grid-template-columns: minmax(180px, 1fr) auto;
    grid-template-rows: auto auto;

    gap: 8px 18px;

    padding: 15px 18px;

    border-radius: 18px;

    background:
        linear-gradient(
            135deg,
            rgba(25,25,35,.97),
            rgba(12,14,20,.98)
        );

    border: 1px solid rgba(255,255,255,.12);

    box-shadow:
        0 15px 50px rgba(0,0,0,.45),
        0 0 0 1px rgba(255,255,255,.025);

    backdrop-filter: blur(18px);
    -webkit-backdrop-filter: blur(18px);

    color: white;
}

/* Botón X para cerrar el reproductor */

.album-player-close {
    position: absolute;
    top: 8px;
    right: 10px;

    width: 30px;
    height: 30px;
    padding: 0;

    border: 0;
    border-radius: 50%;

    background: rgba(255,255,255,.06);
    color: rgba(255,255,255,.65);

    display: grid;
    place-items: center;

    font-size: 16px;
    font-weight: 700;

    cursor: pointer;

    transition:
        background .15s ease,
        color .15s ease,
        transform .15s ease;
}

.album-player-close:hover {
    background: rgba(255,80,95,.18);
    color: white;
    transform: scale(1.08);
}

.album-player-close:active {
    transform: scale(.94);
}

.album-player-title {
    min-width: 0;

    cursor: grab;
    user-select: none;
    -webkit-user-select: none;

    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;

    align-self: center;

    font-size: 14px;
    font-weight: 750;
}

.album-player-controls {
    display: flex;
    align-items: center;
    justify-content: flex-end;

    gap: 9px;
}

.album-player-controls button {
    width: 40px;
    height: 40px;

    padding: 0;

    border: 1px solid rgba(255,255,255,.12);
    border-radius: 50%;

    background: rgba(255,255,255,.07);

    color: white;

    display: grid;
    place-items: center;

    font-size: 16px;

    cursor: pointer;

    transition:
        transform .15s ease,
        background .15s ease,
        border-color .15s ease;
}

.album-player-controls button:hover {
    transform: scale(1.08);

    background: rgba(255,255,255,.14);

    border-color: rgba(255,255,255,.22);
}

.album-player-toggle {
    width: 48px !important;
    height: 48px !important;

    font-size: 20px !important;

    background:
        linear-gradient(
            135deg,
            #7c5cff,
            #5e7cff
        ) !important;

    border-color: transparent !important;

    box-shadow:
        0 6px 20px rgba(92,80,220,.35);
}

.album-player-toggle:hover {
    background:
        linear-gradient(
            135deg,
            #8b6cff,
            #6e8cff
        ) !important;
}

.album-player-position {
    min-width: 48px;

    margin-left: 4px;

    color: var(--muted);

    font-size: 12px;
    font-weight: 750;

    text-align: center;
}

.album-player audio {
    grid-column: 1 / -1;

    width: 100%;
    height: 32px;

    display: block;
}


/* =========================================================
   REPRODUCTOR FLOTANTE DE LISTA SPOTIFY
========================================================= */

.spotify-floating-player {
    position: fixed !important;

    left: 18px;
    right: 18px;
    bottom: 18px;

    z-index: 9999;

    display: grid;
    grid-template-columns: minmax(180px, 1fr);

    gap: 10px;

    padding: 16px 20px;

    border-radius: 18px;

    background:
        linear-gradient(
            135deg,
            rgba(25,25,35,.97),
            rgba(12,14,20,.98)
        );

    border: 1px solid rgba(255,255,255,.12);

    box-shadow:
        0 15px 50px rgba(0,0,0,.45),
        0 0 0 1px rgba(255,255,255,.025);

    backdrop-filter: blur(18px);
    -webkit-backdrop-filter: blur(18px);

    color: white;
}

.spotify-floating-player .player-info {
    min-width: 0;

    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;

    font-size: 14px;
    font-weight: 750;

    padding-right: 25px;
}

.spotify-floating-info {
    display: flex;
    align-items: center;
    justify-content: space-between;

    gap: 12px;

    min-width: 0;
}

.spotify-floating-position {
    flex-shrink: 0;

    font-size: 12px;
    font-weight: 700;

    opacity: .65;
}

/* ---------------------------------------------------------
   BARRA DE PROGRESO
--------------------------------------------------------- */

.spotify-floating-progress {
    display: flex;
    align-items: center;

    width: 100%;

    gap: 10px;

    margin-top: 2px;
}

.spotify-time-current,
.spotify-time-duration {
    width: 38px;

    flex-shrink: 0;

    font-size: 11px;
    font-weight: 700;

    opacity: .7;

    text-align: center;
}

.spotify-progress {
    appearance: none;
    -webkit-appearance: none;

    flex: 1;

    width: 100%;
    height: 6px;

    margin: 0;

    padding: 0;

    border-radius: 10px;

    background: rgba(255,255,255,.18);

    cursor: pointer;

    outline: none;
}

/* Barra WebKit */

.spotify-progress::-webkit-slider-runnable-track {
    height: 6px;

    border-radius: 10px;

    background: rgba(255,255,255,.18);
}

.spotify-progress::-webkit-slider-thumb {
    appearance: none;
    -webkit-appearance: none;

    width: 14px;
    height: 14px;

    margin-top: -4px;

    border-radius: 50%;

    background: #ffffff;

    border: 0;

    box-shadow:
        0 2px 8px rgba(0,0,0,.35);

    cursor: pointer;
}

/* Barra Firefox */

.spotify-progress::-moz-range-track {
    height: 6px;

    border-radius: 10px;

    background: rgba(255,255,255,.18);
}

.spotify-progress::-moz-range-progress {
    height: 6px;

    border-radius: 10px;

    background: rgba(255,255,255,.85);
}

.spotify-progress::-moz-range-thumb {
    width: 14px;
    height: 14px;

    border-radius: 50%;

    background: #ffffff;

    border: 0;

    cursor: pointer;
}

.spotify-progress:hover::-webkit-slider-thumb {
    transform: scale(1.15);
}

.spotify-progress:focus::-webkit-slider-thumb {
    transform: scale(1.15);
}

/* ---------------------------------------------------------
   CONTROLES
--------------------------------------------------------- */

.spotify-floating-controls {
    display: flex;

    align-items: center;
    justify-content: center;

    gap: 12px;

    margin-top: 2px;
}

.spotify-floating-control {
    width: 42px;
    height: 42px;

    border-radius: 50%;

    border: 1px solid rgba(255,255,255,.12);

    background: rgba(255,255,255,.08);

    color: white;

    font-size: 18px;

    cursor: pointer;

    transition:
        transform .15s ease,
        background .15s ease;
}

.spotify-floating-control:hover {
    transform: scale(1.06);

    background: rgba(255,255,255,.15);
}

.spotify-floating-main {
    width: 48px;
    height: 48px;

    font-size: 20px;

    background: rgba(255,255,255,.16);
}

.spotify-floating-close {
    position: absolute;

    top: 8px;
    right: 10px;

    width: 28px;
    height: 28px;

    border: 0;

    border-radius: 50%;

    background: rgba(255,255,255,.07);

    color: rgba(255,255,255,.75);

    font-size: 20px;

    line-height: 1;

    cursor: pointer;
}

.spotify-floating-close:hover {
    background: rgba(255,255,255,.14);

    color: white;
}

/* El audio nativo no hace falta mostrarlo */

.spotify-floating-player audio {
    display: none !important;
}

/* ========================================================= */


.spotify-download-status {
    min-height: 18px;
}

.spotify-download-status-loading {
    color: #f0ad4e;
}

.spotify-download-status-progress {
    color: #4dabf7;
}

.spotify-download-status-success {
    color: #35c759;
}

.spotify-download-status-error {
    color: #ff4d4f;
}

.spotify-download-progress {
    width: 100%;
    height: 5px;
    margin-top: 6px;
    overflow: hidden;
    border-radius: 10px;
    background: rgba(255,255,255,.12);
}

.spotify-download-progress > div {
    height: 100%;
    border-radius: 10px;
    background: #35c759;
    transition: width .3s ease;
}

/* Botón principal de escuchar álbum */

.album-album-play {
    display: inline-flex;
    align-items: center;
    justify-content: center;

    gap: 8px;

    margin: 12px 8px 14px 0;

    padding: 11px 18px;

    border: 0;
    border-radius: 12px;

    background:
        linear-gradient(
            135deg,
            #7c5cff,
            #5e7cff
        );

    color: white;

    font-size: 13px;
    font-weight: 800;

    cursor: pointer;

    box-shadow:
        0 6px 20px rgba(92,80,220,.22);

    transition:
        transform .15s ease,
        filter .15s ease,
        box-shadow .15s ease;
}

.album-album-play:hover {
    transform: translateY(-1px);

    filter: brightness(1.08);

    box-shadow:
        0 8px 24px rgba(92,80,220,.32);
}

.album-album-play.active {
    background:
        linear-gradient(
            135deg,
            #d94f68,
            #b93655
        );

    box-shadow:
        0 6px 20px rgba(190,55,80,.25);
}

/* Botón parar álbum */

.album-stop-all {
    display: inline-flex;
    align-items: center;
    justify-content: center;

    padding: 10px 15px;

    border-radius: 11px;

    background: rgba(255,255,255,.055);

    border: 1px solid rgba(255,255,255,.10);

    color: var(--muted);

    font-size: 12px;
    font-weight: 700;

    cursor: pointer;

    transition:
        background .15s ease,
        color .15s ease;
}

.album-stop-all:hover {
    background: rgba(255,80,95,.10);

    color: #ff7b84;
}

/* Separación para que el reproductor fijo no tape
   las últimas canciones del álbum */

.album-box {
    padding-bottom: 110px;
}

/* Móvil */

@media (max-width: 700px) {

    .album-player {
        left: 10px;
        right: 10px;
        bottom: 10px;

        grid-template-columns: 1fr;

        gap: 9px;

        padding: 13px 14px;

        border-radius: 16px;
    }

    .album-player-title {
        text-align: center;

        font-size: 13px;
    }

    .album-player-controls {
        justify-content: center;
    }

    .album-player-controls button {
        width: 38px;
        height: 38px;
    }

    .album-player-toggle {
        width: 46px !important;
        height: 46px !important;
    }

    .album-player-position {
        margin-left: 3px;
    }

    .album-player audio {
        height: 30px;
    }

    .album-album-play,
    .album-stop-all {
        margin-top: 5px;
    }
}



/* ===== BOTÓN VOLVER ÁLBUM V2 ===== */

.album-back-button {
    display: inline-flex !important;
    align-items: center !important;
    gap: 8px !important;

    margin: 0 0 18px 0 !important;
    padding: 9px 14px !important;

    border: 1px solid rgba(255,255,255,.10) !important;
    border-radius: 11px !important;

    background: rgba(255,255,255,.045) !important;
    color: rgba(255,255,255,.78) !important;

    font-size: 13px !important;
    font-weight: 700 !important;

    cursor: pointer !important;

    box-shadow: none !important;

    transition:
        background .18s ease,
        border-color .18s ease,
        color .18s ease,
        transform .18s ease !important;
}

.album-back-button:hover {
    background: rgba(124,92,255,.12) !important;
    border-color: rgba(124,92,255,.30) !important;
    color: #ffffff !important;
    transform: translateX(-2px) !important;
}

.album-back-button:active {
    transform: translateX(-1px) scale(.98) !important;
}

.album-back-button:focus-visible {
    outline: none !important;
    border-color: rgba(124,92,255,.55) !important;
    box-shadow: 0 0 0 3px rgba(124,92,255,.12) !important;
}


@media (max-width: 600px) {
    .spotify-saved-item {
        max-width: 100%;
        min-width: 0;
        box-sizing: border-box;
        overflow: hidden;
    }

    .spotify-saved-info {
        min-width: 0;
        max-width: 100%;
        overflow: hidden;
    }

    .spotify-saved-name {
        min-width: 0;
        max-width: 100%;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    .spotify-saved-actions {
        flex-shrink: 0;
    }
}

</style>

<style>

/* =========================================================
   TARJETAS DE CANCIONES DEL ÁLBUM — ESTILO MODERNO
========================================================= */

.album-track {
    position: relative !important;

    display: grid !important;
    grid-template-columns: 42px minmax(0, 1fr) auto !important;
    align-items: center !important;
    gap: 14px !important;

    min-height: 62px !important;
    padding: 9px 12px !important;

    border: 1px solid rgba(255,255,255,.065) !important;
    border-radius: 14px !important;

    background:
        linear-gradient(
            135deg,
            rgba(255,255,255,.045),
            rgba(255,255,255,.018)
        ) !important;

    box-shadow:
        0 5px 18px rgba(0,0,0,.10) !important;

    transition:
        background .18s ease,
        border-color .18s ease,
        transform .18s ease,
        box-shadow .18s ease !important;
}

.album-track:hover {
    background:
        linear-gradient(
            135deg,
            rgba(124,92,255,.105),
            rgba(255,255,255,.035)
        ) !important;

    border-color: rgba(124,92,255,.24) !important;

    transform: translateY(-1px) !important;

    box-shadow:
        0 9px 25px rgba(0,0,0,.17) !important;
}


/* Número de pista */

.album-track-number {
    width: 34px !important;
    height: 34px !important;

    display: grid !important;
    place-items: center !important;

    border-radius: 10px !important;

    background: rgba(255,255,255,.055) !important;
    border: 1px solid rgba(255,255,255,.07) !important;

    color: rgba(255,255,255,.52) !important;

    font-size: 12px !important;
    font-weight: 800 !important;

    font-variant-numeric: tabular-nums !important;

    transition:
        background .18s ease,
        color .18s ease,
        border-color .18s ease !important;
}

.album-track:hover .album-track-number {
    background: rgba(124,92,255,.18) !important;
    border-color: rgba(124,92,255,.28) !important;
    color: #fff !important;
}


/* Nombre de canción */

.album-track-name {
    min-width: 0 !important;

    display: flex !important;
    align-items: center !important;
    gap: 9px !important;

    overflow: hidden !important;
}

.album-track-name strong {
    min-width: 0 !important;

    overflow: hidden !important;
    text-overflow: ellipsis !important;
    white-space: nowrap !important;

    color: rgba(255,255,255,.91) !important;

    font-size: 14px !important;
    font-weight: 700 !important;

    letter-spacing: -.1px !important;
}

.album-track-name small {
    flex: 0 0 auto !important;

    margin-left: 0 !important;
    padding: 4px 7px !important;

    border-radius: 7px !important;

    background: rgba(255,255,255,.045) !important;

    color: rgba(255,255,255,.43) !important;

    font-size: 11px !important;
    font-weight: 700 !important;

    font-variant-numeric: tabular-nums !important;
}


/* Botones */

.album-track-buttons {
    display: flex !important;
    align-items: center !important;
    gap: 7px !important;
}

.album-track-buttons button {
    width: 40px !important;
    height: 40px !important;

    display: grid !important;
    place-items: center !important;

    padding: 0 !important;

    border: 1px solid rgba(255,255,255,.08) !important;
    border-radius: 10px !important;

    background: rgba(255,255,255,.055) !important;

    color: rgba(255,255,255,.78) !important;

    font-size: 13px !important;
    font-weight: 800 !important;

    cursor: pointer !important;

    box-shadow: none !important;

    transition:
        background .16s ease,
        border-color .16s ease,
        color .16s ease,
        transform .16s ease !important;
}

.album-track-buttons button:hover {
    background: rgba(124,92,255,.18) !important;
    border-color: rgba(124,92,255,.30) !important;
    color: #fff !important;

    transform: translateY(-1px) !important;
}

.album-track-buttons button:active {
    transform: scale(.94) !important;
}


/* Botón descargar */

.album-track-buttons .album-download {
    min-width: 34px !important;

    background: rgba(255,255,255,.04) !important;
}

.album-track-buttons .album-download:hover {
    background: rgba(70,210,135,.13) !important;
    border-color: rgba(70,210,135,.25) !important;
}


/* Separación entre tarjetas */

.album-box .album-track + .album-track {
    margin-top: 7px !important;
}


/* Móvil */

@media (max-width: 600px) {

    .album-track {
        grid-template-columns: 36px minmax(0, 1fr) auto !important;
        gap: 10px !important;

        min-height: 58px !important;
        padding: 8px 9px !important;

        border-radius: 12px !important;
    }

    .album-track-number {
        width: 30px !important;
        height: 30px !important;

        border-radius: 9px !important;
    }

    .album-track-name {
        gap: 6px !important;
    }

    .album-track-name strong {
        font-size: 13px !important;
    }

    .album-track-name small {
        padding: 3px 6px !important;
        font-size: 10px !important;
    }

    .album-track-buttons {
        gap: 5px !important;
    }

    .album-track-buttons button {
        width: 32px !important;
        height: 32px !important;
    }
}

</style>
<style>

/* PLAY DEL ÁLBUM — LIGERAMENTE MÁS GRANDE */

.album-track-buttons button:first-child {
    width: 40px !important;
    height: 40px !important;
    border-radius: 11px !important;
    font-size: 15px !important;
}

</style>
<style>

/* =========================================================
   PLAY DE CANCIONES DEL ÁLBUM
   ========================================================= */

.album-track-buttons .btn.preview {
    width: 42px !important;
    height: 42px !important;
    min-width: 42px !important;
    min-height: 42px !important;
    padding: 0 !important;
    display: grid !important;
    place-items: center !important;
    border-radius: 12px !important;
    font-size: 16px !important;
    line-height: 1 !important;
}

</style>
<style>

/* =========================================================
   BOTONES DE LAS CANCIONES DEL ÁLBUM — AJUSTE FINAL
   ========================================================= */

.album-track-buttons {
    display: flex !important;
    align-items: center !important;
    justify-content: flex-end !important;
    gap: 10px !important;
    min-width: max-content !important;
    padding-left: 8px !important;
}

/* Play */
.album-track-buttons .btn.preview {
    width: 42px !important;
    height: 42px !important;
    min-width: 42px !important;
    min-height: 42px !important;
    flex: 0 0 42px !important;
    padding: 0 !important;
    display: grid !important;
    place-items: center !important;
    border-radius: 12px !important;
    font-size: 16px !important;
    line-height: 1 !important;
}

/* Descargar */
.album-track-buttons .album-download {
    width: auto !important;
    min-width: 96px !important;
    height: 38px !important;
    min-height: 38px !important;
    flex: 0 0 auto !important;
    padding: 0 14px !important;
    border-radius: 11px !important;
    font-size: 12px !important;
    font-weight: 800 !important;
    white-space: nowrap !important;
}

/* Un poco más de separación visual al pasar por encima */
.album-track-buttons .btn.preview:hover,
.album-track-buttons .album-download:hover {
    transform: translateY(-1px) !important;
}

/* Móvil */
@media (max-width: 600px) {
    .album-track-buttons {
        gap: 7px !important;
        padding-left: 5px !important;
    }

    .album-track-buttons .btn.preview {
        width: 38px !important;
        height: 38px !important;
        min-width: 38px !important;
        min-height: 38px !important;
        flex-basis: 38px !important;
        border-radius: 10px !important;
    }

    .album-track-buttons .album-download {
        min-width: 82px !important;
        height: 36px !important;
        min-height: 36px !important;
        padding: 0 10px !important;
        font-size: 11px !important;
    }
}

</style>
<style>

/* =========================================================
   AJUSTE PLAY + DESCARGA ÁLBUM
   ========================================================= */

.album-track-buttons {
    display: flex !important;
    align-items: center !important;
    justify-content: flex-end !important;
    gap: 12px !important;
    min-width: 170px !important;
    padding-left: 12px !important;
    flex-shrink: 0 !important;
}

/* Play ligeramente más grande */
.album-track-buttons .btn.preview {
    width: 44px !important;
    height: 44px !important;
    min-width: 44px !important;
    min-height: 44px !important;
    flex: 0 0 44px !important;
    padding: 0 !important;
    display: grid !important;
    place-items: center !important;
    border-radius: 13px !important;
    font-size: 17px !important;
    line-height: 1 !important;
}

/* Descargar: espacio estable incluso cuando cambia el texto */
.album-track-buttons .album-download {
    width: 100px !important;
    min-width: 100px !important;
    max-width: 100px !important;
    height: 40px !important;
    min-height: 40px !important;
    flex: 0 0 100px !important;
    padding: 0 10px !important;
    border-radius: 11px !important;
    font-size: 12px !important;
    font-weight: 800 !important;
    white-space: nowrap !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
}

/* En pantallas pequeñas */
@media (max-width: 600px) {
    .album-track-buttons {
        min-width: 145px !important;
        gap: 8px !important;
        padding-left: 6px !important;
    }

    .album-track-buttons .btn.preview {
        width: 40px !important;
        height: 40px !important;
        min-width: 40px !important;
        min-height: 40px !important;
        flex-basis: 40px !important;
        font-size: 15px !important;
    }

    .album-track-buttons .album-download {
        width: 88px !important;
        min-width: 88px !important;
        max-width: 88px !important;
        flex-basis: 88px !important;
        height: 38px !important;
        min-height: 38px !important;
        font-size: 11px !important;
    }
}

</style>
<style>

/* =========================================================
   ÁLBUM — SEPARACIÓN PLAY / DESCARGAR
   ========================================================= */

.album-track-buttons {
    display: flex !important;
    align-items: center !important;
    justify-content: flex-end !important;
    gap: 18px !important;
    min-width: 190px !important;
    padding-left: 14px !important;
    padding-right: 4px !important;
    flex-shrink: 0 !important;
}

/* Play / previsualización */
.album-track-buttons .btn.preview {
    width: 44px !important;
    height: 44px !important;
    min-width: 44px !important;
    min-height: 44px !important;
    flex: 0 0 44px !important;
    padding: 0 !important;
    display: grid !important;
    place-items: center !important;
    border-radius: 13px !important;
    font-size: 17px !important;
    line-height: 1 !important;
}

/* Descargar más a la derecha */
.album-track-buttons .album-download {
    width: 100px !important;
    min-width: 100px !important;
    max-width: 100px !important;
    height: 40px !important;
    min-height: 40px !important;
    flex: 0 0 100px !important;
    margin-left: 4px !important;
    padding: 0 12px !important;
    border-radius: 11px !important;
    font-size: 12px !important;
    font-weight: 800 !important;
    white-space: nowrap !important;
}

/* Móvil */
@media (max-width: 600px) {
    .album-track-buttons {
        gap: 10px !important;
        min-width: 150px !important;
        padding-left: 7px !important;
    }

    .album-track-buttons .btn.preview {
        width: 40px !important;
        height: 40px !important;
        min-width: 40px !important;
        min-height: 40px !important;
        flex-basis: 40px !important;
        font-size: 15px !important;
    }

    .album-track-buttons .album-download {
        width: 88px !important;
        min-width: 88px !important;
        max-width: 88px !important;
        flex-basis: 88px !important;
    }
}

</style>
<style>

/* =========================================================
   ÁLBUM — BOTONES DE PREVISUALIZACIÓN Y DESCARGA
   ========================================================= */

.album-track-buttons {
    display: flex !important;
    align-items: center !important;
    justify-content: flex-end !important;
    gap: 28px !important;
    min-width: 220px !important;
    padding-left: 18px !important;
    padding-right: 8px !important;
    flex-shrink: 0 !important;
}

/* Botón Play / Preparando / Cerrar */
.album-track-buttons .btn.preview {
    width: 100px !important;
    min-width: 100px !important;
    max-width: 100px !important;
    height: 44px !important;
    min-height: 44px !important;
    flex: 0 0 100px !important;

    display: grid !important;
    place-items: center !important;

    padding: 0 8px !important;
    border-radius: 13px !important;

    font-size: 13px !important;
    font-weight: 800 !important;
    line-height: 1 !important;

    white-space: nowrap !important;
    overflow: hidden !important;
}

/* Descargar */
.album-track-buttons .album-download {
    width: 100px !important;
    min-width: 100px !important;
    max-width: 100px !important;
    height: 40px !important;
    min-height: 40px !important;
    flex: 0 0 100px !important;

    padding: 0 12px !important;
    border-radius: 11px !important;

    font-size: 12px !important;
    font-weight: 800 !important;
    white-space: nowrap !important;
}

/* Móvil */
@media (max-width: 600px) {
    .album-track-buttons {
        gap: 12px !important;
        min-width: 195px !important;
        padding-left: 8px !important;
        padding-right: 3px !important;
    }

    .album-track-buttons .btn.preview {
        width: 88px !important;
        min-width: 88px !important;
        max-width: 88px !important;
        flex-basis: 88px !important;
        height: 40px !important;
        min-height: 40px !important;
        font-size: 11px !important;
    }

    .album-track-buttons .album-download {
        width: 88px !important;
        min-width: 88px !important;
        max-width: 88px !important;
        flex-basis: 88px !important;
        height: 38px !important;
        min-height: 38px !important;
    }
}

</style>
<style>

/* =========================================================
   ÚLTIMO AJUSTE — BOTONES ÁLBUM
   ========================================================= */

.album-track-buttons {
    justify-content: flex-end !important;
    min-width: 235px !important;
    padding-left: 24px !important;
    padding-right: 2px !important;
}

/* Play ligeramente más pequeño */
.album-track-buttons .btn.preview {
    width: 92px !important;
    min-width: 92px !important;
    max-width: 92px !important;
    flex-basis: 92px !important;
    height: 42px !important;
    min-height: 42px !important;
    font-size: 12px !important;
}

/* Descargar mantiene su tamaño */
.album-track-buttons .album-download {
    width: 100px !important;
    min-width: 100px !important;
    max-width: 100px !important;
    flex-basis: 100px !important;
}

</style>

<style>
/* =========================================================
   DISTRIBUCIÓN COMPLETA DE LA TARJETA DEL ÁLBUM
========================================================= */

.album-track {
    display: grid !important;

    /* número | miniatura | información | botones */
    grid-template-columns:
        42px
        42px
        minmax(0, 1fr)
        auto !important;

    align-items: center !important;
    gap: 14px !important;
}

/* Miniatura */
.album-track-thumb {
    width: 42px !important;
    height: 42px !important;
    object-fit: cover !important;
    border-radius: 9px !important;
    flex-shrink: 0 !important;
}

/* Toda la información aprovecha el espacio central */
.album-track-name {
    min-width: 0 !important;
    width: 100% !important;
    overflow: hidden !important;
}

/* Título */
.album-track-name strong {
    display: inline-block !important;
    max-width: 100% !important;
    min-width: 0 !important;

    overflow: hidden !important;
    text-overflow: ellipsis !important;
    white-space: nowrap !important;
}

/* Estado de la canción */
.album-track-status {
    display: block !important;
    max-width: 100% !important;

    overflow: hidden !important;
    text-overflow: ellipsis !important;
    white-space: nowrap !important;
}

/* Progreso */
.album-track-progress {
    min-width: 0 !important;
    width: 100% !important;
}

/* Los botones conservan su tamaño y quedan separados */
.album-track-buttons {
    flex-shrink: 0 !important;
    white-space: nowrap !important;
    justify-self: end !important;
}
</style>


<style>
/* =========================================================
   TARJETAS DEL ÁLBUM — AJUSTE PARA MÓVIL
   No modifica el diseño de PC
========================================================= */

@media (max-width: 700px) {

    .album-track {
        grid-template-columns:
            34px
            minmax(0, 1fr) !important;

        gap: 10px !important;
        padding: 10px !important;
    }

    /* La miniatura pasa a formar parte de la zona de información */
    .album-track-thumb {
        width: 34px !important;
        height: 34px !important;
    }

    /* Información ocupa todo el ancho disponible */
    .album-track-name {
        min-width: 0 !important;
        width: 100% !important;
    }

    /* Los botones pasan a una fila completa debajo */
    .album-track-buttons {
        grid-column: 1 / -1 !important;

        width: 100% !important;

        display: flex !important;
        justify-content: flex-end !important;
        align-items: center !important;

        gap: 10px !important;

        margin-top: 4px !important;
    }

    .album-track-buttons button {
        flex-shrink: 0 !important;
    }

    .album-track-status {
        max-width: 100% !important;
    }

    .album-track-progress {
        max-width: 100% !important;
    }
}
</style>


<style>
@media (max-width: 700px) {

    .album-track-progress {
        display: block !important;
        width: 100% !important;
        min-width: 0 !important;
        margin-top: 6px !important;
    }

    .album-track-progress-bar {
        display: block !important;
        width: 100% !important;
        min-width: 0 !important;
        height: 5px !important;
        overflow: hidden !important;
    }

    .album-track-progress-fill {
        display: block !important;
        height: 100% !important;
        max-width: 100% !important;
    }

    .album-track-progress-text {
        display: block !important;
        margin-top: 3px !important;
    }
}
</style>


<style>
@media (max-width: 700px) {

    .album-track {
        display: grid !important;
        grid-template-columns: 34px minmax(0, 1fr) !important;
        gap: 10px !important;
        padding: 10px !important;
    }

    .album-track-number {
        grid-column: 1 !important;
        grid-row: 1 !important;
    }

    .album-track-thumb {
        display: none !important;
    }

    .album-track-name {
        grid-column: 2 !important;
        grid-row: 1 !important;

        display: block !important;
        width: 100% !important;
        min-width: 0 !important;
        overflow: visible !important;
    }

    .album-track-name strong {
        display: inline-block !important;
        max-width: calc(100% - 55px) !important;
    }

    .album-track-status {
        display: block !important;
        width: 100% !important;
        max-width: 100% !important;
        overflow: hidden !important;
        text-overflow: ellipsis !important;
        white-space: nowrap !important;
        margin-top: 4px !important;
    }

    .album-track-progress {
        display: block !important;
        width: 100% !important;
        min-width: 0 !important;
        margin-top: 7px !important;
    }

    .album-track-progress-bar {
        display: block !important;
        width: 100% !important;
        height: 5px !important;
        min-width: 0 !important;
        overflow: hidden !important;
    }

    .album-track-progress-fill {
        display: block !important;
        height: 100% !important;
    }

    .album-track-progress-text {
        display: block !important;
        margin-top: 3px !important;
    }

    .album-track-buttons {
        grid-column: 1 / -1 !important;
        grid-row: 2 !important;

        display: flex !important;
        justify-content: flex-end !important;
        align-items: center !important;

        width: 100% !important;
        min-width: 0 !important;
        margin-top: 3px !important;

        gap: 10px !important;
    }

    .album-track-buttons button {
        flex-shrink: 0 !important;
    }
}
</style>


<style>
/* =========================================================
   PORTADA GRANDE DE CADA CANCIÓN — SOLO MÓVIL
========================================================= */

@media (max-width: 700px) {

    .album-track {
        display: grid !important;
        grid-template-columns: 34px minmax(0, 1fr) !important;
        grid-template-rows: auto auto auto !important;
    }

    /* Portada grande arriba de toda la tarjeta */
    .album-track-thumb {
        display: block !important;

        grid-column: 1 / -1 !important;
        grid-row: 1 !important;

        width: 100% !important;
        height: auto !important;
        aspect-ratio: 16 / 9 !important;

        object-fit: cover !important;

        border-radius: 12px !important;

        margin: 0 0 8px 0 !important;

        justify-self: stretch !important;
    }

    /* Número */
    .album-track-number {
        grid-column: 1 !important;
        grid-row: 2 !important;
    }

    /* Información */
    .album-track-name {
        grid-column: 2 !important;
        grid-row: 2 !important;

        min-width: 0 !important;
        width: 100% !important;
    }

    /* Botones */
    .album-track-buttons {
        grid-column: 1 / -1 !important;
        grid-row: 3 !important;

        width: 100% !important;
        min-width: 0 !important;

        display: flex !important;
        justify-content: flex-end !important;
        align-items: center !important;

        margin-top: 5px !important;
    }
}
</style>



<style>

.spotify-card {
    margin-top: 22px;
}

.spotify-import {
    display: flex;
    flex-direction: column;
    gap: 14px;
}

.spotify-file-label {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: fit-content;
    min-height: 44px;
    padding: 0 18px;
    border-radius: 12px;
    cursor: pointer;
    font-weight: 800;
    background: var(--accent);
    color: white;
    transition: transform .15s ease, opacity .15s ease;
}

.spotify-file-label:hover {
    transform: translateY(-1px);
    opacity: .92;
}

.spotify-info {
    padding: 14px 16px;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: rgba(255,255,255,.035);
}

.spotify-info strong {
    display: block;
    margin-bottom: 4px;
}

.spotify-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
}


.spotify-saved-lists {
    margin-top: 18px;
    margin-bottom: 18px;
    padding: 14px;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: rgba(255,255,255,.025);
}

.spotify-saved-title {
    font-weight: 800;
    margin-bottom: 10px;
}

.spotify-saved-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
}

.spotify-saved-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 10px 12px;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: rgba(255,255,255,.025);
}

.spotify-saved-info {
    min-width: 0;
}

.spotify-saved-name {
    font-weight: 750;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.spotify-saved-meta {
    margin-top: 3px;
    color: var(--muted);
    font-size: 12px;
}

.spotify-saved-actions {
    display: flex;
    gap: 7px;
    flex-wrap: wrap;
    justify-content: flex-end;
}

.spotify-saved-empty {
    color: var(--muted);
    font-size: 13px;
}

@media (max-width: 700px) {
    .spotify-saved-item {
        align-items: flex-start;
        flex-direction: column;
    }

    .spotify-saved-actions {
        justify-content: flex-start;
    }
}


.spotify-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
}

.spotify-track {
    display: grid;
    grid-template-columns: 34px minmax(0, 1fr) auto;
    align-items: center;
    gap: 12px;
    padding: 11px 13px;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: rgba(255,255,255,.025);
}

.spotify-track:hover {
    background: rgba(255,255,255,.045);
}

.spotify-check {
    width: 20px;
    height: 20px;
    cursor: pointer;
}

.spotify-track-info {
    min-width: 0;
}

.spotify-track-title {
    font-weight: 750;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.spotify-track-meta {
    margin-top: 3px;
    color: var(--muted);
    font-size: 12px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.spotify-number {
    color: var(--muted);
    font-size: 12px;
    text-align: right;
}


.spotify-track-player {
    display: flex;
    gap: 8px;
    margin-top: 8px;
    flex-wrap: wrap;
}

.spotify-track-player .btn {
    min-height: 34px;
    padding: 7px 12px;
    font-size: 12px;
}

@media (max-width: 600px) {

    .spotify-track-player {
        width: 100%;
    }

    .spotify-track-player .btn {
        flex: 1;
        min-width: 100px;
    }

}

.spotify-playing {
    border-color: var(--accent) !important;
    background: rgba(255,255,255,.07) !important;
}

.spotify-status {
    color: var(--muted);
    font-size: 13px;
}

@media (max-width: 600px) {

    .spotify-track {
        grid-template-columns: 30px minmax(0, 1fr);
        gap: 9px;
    }

    .spotify-number {
        display: none;
    }

    .spotify-actions {
        flex-direction: column;
    }

    .spotify-actions button {
        width: 100%;
    }

}

</style>


</head>

<body>

<header class="header">

    <div class="header-inner">

        <div class="logo">

            <div class="logo-icon">
                🎵
            </div>

            <div>
                Music Downloader
            </div>

        </div>

        <div class="subtitle">
            YouTube → MP3 → MusicBrainz → Navidrome
        </div>

    </div>

</header>

<main class="container">

    <section class="hero">

        <h1>
            Tu música, en un solo sitio.
        </h1>

        <p>
            Busca canciones, escúchalas y añádelas directamente a tu biblioteca Navidrome.
        </p>

    </section>

    <div class="search">

        <input
            id="search"
            placeholder="Buscar artista, canción o álbum..."
            autocomplete="off"
            spellcheck="false"
        >

        <button onclick="searchMusic()">
            🔎 Buscar
        </button>

    </div>



    <section class="card spotify-card">

        <div class="card-header">

            <div>

                <div class="card-title">
                    🎵 Spotify
                </div>

                <div class="card-subtitle">
                    Importa tus playlists en CSV, TXT o JSON
                </div>

            </div>

        </div>

        <div class="spotify-import">

            <label
                class="spotify-file-label"
                for="spotifyFile">

                📂 Seleccionar playlist

            </label>

            <input
                id="spotifyFile"
                type="file"
                accept=".csv,.txt,.json,text/csv,text/plain,application/json"
                style="display:none"
                onchange="importSpotifyFile(this)"
            >

            <div
                id="spotifyInfo"
                class="spotify-info"
                style="display:none">
            </div>

            <div class="spotify-actions">

                <button
                    class="btn preview"
                    onclick="window.open('https://exportify.net/', '_blank', 'noopener,noreferrer')">
                    🌐 Abrir Exportify
                </button>

            </div>

            <div
                id="spotifyActions"
                class="spotify-actions"
                style="display:none">

                <button
                    class="btn preview"
                    onclick="spotifySelectAll()">
                    ☑ Seleccionar todas
                </button>

                <button
                    class="btn download"
                    onclick="spotifyDownloadSelected()">
                    ↓ Descargar seleccionadas
                </button>

                <button
                    class="btn preview"
                    onclick="spotifyPlayPlaylist()">
                    ▶ Escuchar lista
                </button>

                <button
                    class="btn cancel"
                    onclick="spotifyStopPlaylist()">
                    ⏹ Parar lista
                </button>

            </div>

            <div class="spotify-saved-lists">

                <div class="spotify-saved-title">
                    📚 Mis listas guardadas
                </div>

                <div
                    id="spotifySavedLists"
                    class="spotify-saved-list">
                </div>

            </div>


            <div
                id="spotifyList"
                class="spotify-list">
            </div>

            <div
                id="spotifyStatus"
                class="spotify-status">
            </div>

        </div>

    </section>

    <div
        id="results"
        class="results">
    </div>

    <div
        id="pagination"
        class="pagination"
        style="display:none">

        <button
            id="previous"
            onclick="previousPage()">
            ← Atrás
        </button>

        <div
            id="page"
            class="page">
        </div>

        <button
            id="next"
            onclick="nextPage()">
            Siguiente →
        </button>

    </div>

    <section
        id="downloadQueue"
        class="card"
        style="display:none !important">

        <div class="card-header">

            <div>

                <div class="card-title">
                    📥 Descargas
                </div>

                <div class="card-subtitle">
                    Estado de las canciones
                </div>

            </div>

        </div>

        <div id="queueList"></div>

    </section>

    <section class="card">

        <div class="library">

            <div class="library-icon">
                🎶
            </div>

            <div>

                <strong>
                    Biblioteca Navidrome
                </strong>

                <span>
                    Las canciones descargadas se organizan automáticamente y quedan disponibles en tu biblioteca.
                </span>

            </div>

        </div>

    </section>

</main>

<div
    id="notice"
    class="notice">
</div>

<script>

let allResults = [];

let currentPage = 1;

const resultsPerPage = 50;

let currentPlayer = null;


async function searchMusic() {

    const input =
        document.getElementById("search");

    const q =
        input.value.trim();

    if (!q) {

        input.focus();

        return;
    }

    stopPreview();

    const results =
        document.getElementById("results");

    results.innerHTML = `
        <div class="card">
            <div class="loading">
                <div class="spinner"></div>
                <span>Buscando música...</span>
            </div>
        </div>
    `;

    document
        .getElementById("pagination")
        .style.display = "none";

    try {

        const response =
            await fetch(
                "/api/search?q=" +
                encodeURIComponent(q)
            );

        const data =
            await response.json();

        if (data.error) {

            results.innerHTML =
                "<div class='card error'>" +
                escapeHtml(data.error) +
                "</div>";

            return;
        }

        allResults =
            data.results || [];

        currentPage = 1;

        renderPage();

    } catch (error) {

        results.innerHTML = `
            <div class="card error">
                No se pudo conectar con el servidor.
            </div>
        `;
    }
}


function renderPage() {

    stopPreview();

    const results =
        document.getElementById("results");

    results.innerHTML = "";

    if (!allResults.length) {

        results.innerHTML = `
            <div class="card">
                <div class="empty">
                    🎵 No se encontraron resultados.
                </div>
            </div>
        `;

        return;
    }

    const start =
        (currentPage - 1) *
        resultsPerPage;

    const end =
        start +
        resultsPerPage;

    const pageResults =
        allResults.slice(start, end);

    pageResults.forEach(item => {

        const div =
            document.createElement("div");

        div.className = "result";

        div.innerHTML = `

            <div class="thumbnail-wrap">

                <img
                    class="thumbnail"
                    src="${escapeAttribute(item.thumbnail || '')}"
                    loading="lazy"
                    onerror="this.style.opacity='.25'"
                >

            </div>

            <div class="info">

                <div class="title">
                    ${escapeHtml(item.title)}
                </div>

                <div class="meta">

                    <span class="meta-pill">
                        ⏱️ ${formatDuration(item.duration)}
                    </span>

                    <span class="channel">
                        ${escapeHtml(item.channel || "YouTube")}
                    </span>

                </div>

                <div
                    class="status"
                    id="status-${escapeAttribute(item.id)}">
                </div>

                <div
                    class="result-progress"
                    id="progress-${escapeAttribute(item.id)}"
                    style="display:none">

                    <div class="result-progress-bar">
                        <div
                            class="result-progress-fill"
                            style="width:0%">
                        </div>
                    </div>

                    <small class="result-progress-text">
                        0%
                    </small>

                </div>

            </div>

            <div class="buttons">

                <button
                    class="btn preview"
                    onclick="previewMusic('${escapeAttribute(item.id)}', this)">
                    ▶ Escuchar
                </button>

                <button
                    class="btn download"
                    id="download-${escapeAttribute(item.id)}"
                    onclick="downloadMusic('${escapeAttribute(item.id)}', this)">
                    ↓ Descargar
                </button>

                <button
                    class="btn cancel"
                    id="cancel-${escapeAttribute(item.id)}"
                    onclick="cancelDownloadByVideo('${escapeAttribute(item.id)}')"
                    style="display:none">
                    ⏹ Cancelar
                </button>

            </div>

        `;

        results.appendChild(div);

    });

    updatePagination();
}



let albumPlayerState = {
    active: false,
    tracks: [],
    index: 0,
    audio: null,
    player: null
};


window.stopAlbumPlayback = function() {

    if (albumPlayerState.audio) {
        albumPlayerState.audio.pause();
        albumPlayerState.audio.src = "";
    }

    if (albumPlayerState.player) {
        albumPlayerState.player.remove();
    }

    albumPlayerState = {
        active: false,
        tracks: [],
        index: 0,
        audio: null,
        player: null
    };

    document
        .querySelectorAll(".album-album-play.active")
        .forEach(function(button) {
            button.classList.remove("active");
            button.innerText = "▶▶ Escuchar álbum";
        });
}


async function playAlbumFromIndex(index) {

    const tracks = window.currentAlbumTracks || [];

    if (!tracks.length) {
        return;
    }

    if (index < 0) {
        index = 0;
    }

    if (index >= tracks.length) {
        stopAlbumPlayback();
        return;
    }

    const track = tracks[index];

    if (!track || !track.id) {

        if (index + 1 < tracks.length) {
            playAlbumFromIndex(index + 1);
        }

        return;
    }

    albumPlayerState.active = true;
    albumPlayerState.tracks = tracks;
    albumPlayerState.index = index;

    if (albumPlayerState.player) {
        albumPlayerState.player.remove();
    }

    const albumBox =
        document.querySelector(".album-box");

    if (!albumBox) {
        return;
    }

    const player =
        document.createElement("div");

    player.className = "player album-player";

    player.innerHTML = `

        <button
            type="button"
            class="album-player-close"
            title="Cerrar reproductor"
            aria-label="Cerrar reproductor">
            ✕
        </button>

        <div class="album-player-title">
            🎵 Preparando canción...
        </div>

        <div class="album-player-controls">

            <button
                type="button"
                class="album-player-prev"
                title="Canción anterior">
                ⏮
            </button>

            <button
                type="button"
                class="album-player-toggle"
                title="Reproducir / Pausar">
                ▶
            </button>

            <button
                type="button"
                class="album-player-next"
                title="Siguiente canción">
                ⏭
            </button>

            <span class="album-player-position">
                ${index + 1} / ${tracks.length}
            </span>

        </div>

        <audio
            controls
            preload="auto">
        </audio>
    `;

    albumBox.prepend(player);

    /* =========================================================
       ARRASTRAR REPRODUCTOR DEL ÁLBUM
       Solo se arrastra desde el título
    ========================================================= */

    const dragHandle =
        player.querySelector(".album-player-title");

    if (dragHandle) {

        let dragging = false;
        let offsetX = 0;
        let offsetY = 0;

        dragHandle.addEventListener("pointerdown", function(event) {

            if (event.button !== 0) {
                return;
            }

            const rect = player.getBoundingClientRect();

            dragging = true;

            offsetX = event.clientX - rect.left;
            offsetY = event.clientY - rect.top;

            player.style.left = rect.left + "px";
            player.style.top = rect.top + "px";
            player.style.right = "auto";
            player.style.bottom = "auto";

            dragHandle.style.cursor = "grabbing";

            try {
                dragHandle.setPointerCapture(event.pointerId);
            } catch (e) {}

            event.preventDefault();
        });

        dragHandle.addEventListener("pointermove", function(event) {

            if (!dragging) {
                return;
            }

            const rect = player.getBoundingClientRect();

            let left =
                event.clientX -
                offsetX;

            let top =
                event.clientY -
                offsetY;

            const maxLeft =
                window.innerWidth -
                rect.width;

            const maxTop =
                window.innerHeight -
                rect.height;

            left = Math.max(
                0,
                Math.min(left, maxLeft)
            );

            top = Math.max(
                0,
                Math.min(top, maxTop)
            );

            player.style.left = left + "px";
            player.style.top = top + "px";
        });

        function stopDragging(event) {

            if (!dragging) {
                return;
            }

            dragging = false;

            dragHandle.style.cursor = "grab";

            try {
                dragHandle.releasePointerCapture(event.pointerId);
            } catch (e) {}
        }

        dragHandle.addEventListener(
            "pointerup",
            stopDragging
        );

        dragHandle.addEventListener(
            "pointercancel",
            stopDragging
        );
    }

    albumPlayerState.player = player;

    const audio =
        player.querySelector("audio");

    albumPlayerState.audio = audio;

    player.querySelector(
        ".album-player-close"
    ).onclick = function() {
        stopAlbumPlayback();
    };

    const title =
        track.title ||
        track.name ||
        "Canción";

    const albumTitle =
        window.currentAlbumTitle ||
        "Álbum";

    player.querySelector(
        ".album-player-title"
    ).innerText =
        "🎵 " +
        title +
        " · " +
        albumTitle;

    player.querySelector(
        ".album-player-position"
    ).innerText =
        (index + 1) +
        " / " +
        tracks.length;

    player.querySelector(
        ".album-player-prev"
    ).onclick = function() {

        if (albumPlayerState.index > 0) {
            playAlbumFromIndex(
                albumPlayerState.index - 1
            );
        }
    };

    player.querySelector(
        ".album-player-next"
    ).onclick = function() {

        if (
            albumPlayerState.index + 1 <
            albumPlayerState.tracks.length
        ) {
            playAlbumFromIndex(
                albumPlayerState.index + 1
            );
        }
    };

    player.querySelector(
        ".album-player-toggle"
    ).onclick = function() {

        if (audio.paused) {
            audio.play().catch(() => {});
        } else {
            audio.pause();
        }
    };

    audio.onplay = function() {

        player.querySelector(
            ".album-player-toggle"
        ).innerText = "⏸";
    };

    audio.onpause = function() {

        player.querySelector(
            ".album-player-toggle"
        ).innerText = "▶";
    };

    audio.onended = function() {

        if (
            albumPlayerState.active &&
            albumPlayerState.index + 1 <
            albumPlayerState.tracks.length
        ) {
            playAlbumFromIndex(
                albumPlayerState.index + 1
            );
        } else {
            stopAlbumPlayback();
        }
    };

    try {

        const response =
            await fetch(
                "/api/preview?id=" +
                encodeURIComponent(track.id)
            );

        const data =
            await response.json();

        if (data.error) {

            player.querySelector(
                ".album-player-title"
            ).innerText =
                "❌ " + data.error;

            return;
        }

        audio.src = data.url;

        player.querySelector(
            ".album-player-title"
        ).innerText =
            "🎧 " +
            title +
            " · " +
            albumTitle;

        audio.play().catch(() => {});

    } catch (error) {

        player.querySelector(
            ".album-player-title"
        ).innerText =
            "❌ No se pudo preparar la canción.";
    }
}


function albumStopAll() {

    stopAlbumPlayback();

}

function playCompleteAlbum() {

    // PLAY / STOP
    if (albumPlayerState && albumPlayerState.active === true) {
        stopAlbumPlayback();
        return;
    }

    const tracks =
        window.currentAlbumTracks || [];

    if (!tracks.length) {
        return;
    }

    const firstPlayable =
        tracks.findIndex(function(track) {
            return track && track.id;
        });

    if (firstPlayable === -1) {

        alert(
            "Todavía se están buscando las canciones del álbum."
        );

        return;
    }

    stopPreview();

    document
        .querySelectorAll(".album-album-play")
        .forEach(function(button) {
            button.classList.add("active");
            button.innerText = "⏹ Parar álbum";
        });

    playAlbumFromIndex(firstPlayable);
}

async function previewMusic(id, button) {

    if (currentPlayer === id) {

        stopPreview();

        return;
    }

    stopPreview();

    currentPlayer = id;

    button.disabled = true;
    button.innerText = "⏳ Preparando";

    const result =
        button.closest(".result");

    const player =
        document.createElement("div");

    player.className = "player";

    player.innerHTML = `

        <div class="player-info">
            🎧 Preparando previsualización...
        </div>

        <audio
            controls
            autoplay
            preload="auto">
        </audio>

    `;

    result.appendChild(player);

    try {

        const response =
            await fetch(
                "/api/preview?id=" +
                encodeURIComponent(id)
            );

        const data =
            await response.json();

        if (data.error) {

            player.innerHTML =
                "<div class='error'>" +
                escapeHtml(data.error) +
                "</div>";

            button.disabled = false;
            button.innerText = "▶ Escuchar";

            currentPlayer = null;

            return;
        }

        const audio =
            player.querySelector("audio");

        audio.src = data.url;

        audio.play().catch(() => {});

        button.disabled = false;

        button.classList.add("active");

        button.innerText =
            "⏹ Cerrar";

        const info =
            player.querySelector(".player-info");

        info.innerText =
            "🎧 Previsualización · máximo 30 segundos";

    } catch (error) {

        player.innerHTML =
            "<div class='error'>" +
            "No se pudo preparar la previsualización." +
            "</div>";

        button.disabled = false;
        button.innerText = "▶ Escuchar";

        currentPlayer = null;
    }
}


function stopPreview() {

    if (albumPlayerState && albumPlayerState.active) {
        stopAlbumPlayback();
    }

    document
        .querySelectorAll(".player")
        .forEach(player => {

            const audio =
                player.querySelector("audio");

            if (audio) {

                audio.pause();

                audio.src = "";

            }

            player.remove();

        });

    document
        .querySelectorAll(".preview.active")
        .forEach(button => {

            button.classList.remove("active");

            button.innerText =
                "▶ Escuchar";

            button.disabled = false;

        });

    currentPlayer = null;
}


function updatePagination() {

    const totalPages =
        Math.ceil(
            allResults.length /
            resultsPerPage
        );

    const pagination =
        document.getElementById("pagination");

    pagination.style.display =
        totalPages > 1
        ? "flex"
        : "none";

    document
        .getElementById("page")
        .innerText =
        `Página ${currentPage} de ${totalPages}`;

    document
        .getElementById("previous")
        .disabled =
        currentPage <= 1;

    document
        .getElementById("next")
        .disabled =
        currentPage >= totalPages;
}


async function cancelDownload(job) {

    if (!confirm("¿Detener esta descarga?")) {
        return;
    }

    try {

        const response =
            await fetch(
                "/api/cancel?id=" +
                encodeURIComponent(job)
            );

        const data =
            await response.json();

        if (data.error) {

            alert(data.error);

            return;
        }

        updateQueue();

    } catch (error) {

        alert(
            "No se pudo detener la descarga."
        );
    }
}



async function clearDownloadHistory() {

    if (!confirm(
        "¿Borrar todo el historial de descargas?\n\n" +
        "Los archivos descargados NO se borrarán."
    )) {
        return;
    }

    try {

        const response =
            await fetch(
                "/api/history/clear",
                {
                    method: "POST"
                }
            );

        const data =
            await response.json();

        if (!response.ok || data.error) {

            alert(
                data.error ||
                "No se pudo borrar el historial."
            );

            return;
        }

        updateQueue();

    } catch (error) {

        console.error(
            "Error borrando historial:",
            error
        );

        alert(
            "No se pudo borrar el historial."
        );
    }
}


async function updateQueue() {

    try {

        const response =
            await fetch("/api/queue");

        const data =
            await response.json();

        const panel =
            document.getElementById(
                "downloadQueue"
            );

        const list =
            document.getElementById(
                "queueList"
            );

        const queue =
            data.queue || [];

        const historyData =
            data.history || [];

        if (
            queue.length === 0 &&
            historyData.length === 0
        ) {

            panel.style.display = "none";

            return;
        }

        panel.style.display = "block";

        let html = "";

        queue.forEach(item => {

            const progress =
                item.progress || 0;

            let icon = "🕐";

            if (item.status === "running") {
                icon = "⬇️";
            }

            if (item.status === "queued") {
                icon = "🕐";
            }

            html += `

                <div class="queue-item">

                    <div class="queue-top">

                        <div>

                            <div class="queue-title">
                                ${icon}
                                ${escapeHtml(
                                    item.title || item.id
                                )}
                            </div>

                            <div class="queue-status">
                                ${escapeHtml(
                                    item.message || ""
                                )}
                            </div>

                        </div>

                        <div class="queue-percent">
                            ${progress}%
                        </div>

                    </div>

                    <div class="progress-bar">

                        <div
                            class="progress-fill"
                            style="width:${progress}%">
                        </div>

                    </div>

                </div>

            `;

        });

        if (historyData.length) {

            html += `
                <div class="history-header">

                    <div class="history-title">
                        📜 Historial reciente
                    </div>

                    <button
                        type="button"
                        class="history-clear-button"
                        onclick="clearDownloadHistory()">
                        🗑 Borrar historial
                    </button>

                </div>
            `;

            historyData
                .slice()
                .reverse()
                .slice(0, 10)
                .forEach(item => {

                    let icon = "❌";

                    if (item.status === "done") {
                        icon = "✅";
                    }

                    if (item.status === "cancelled") {
                        icon = "⏹";
                    }

                    html += `

                        <div class="history-item">

                            <span class="history-status">
                                ${icon}
                            </span>

                            <span>
                                ${escapeHtml(
                                    item.title || item.id
                                )}
                            </span>

                            <span>
                                · ${escapeHtml(
                                    item.time || ""
                                )}
                            </span>

                        </div>

                    `;

                });

        }

        list.innerHTML = html;

        document
            .querySelectorAll("[id^='cancel-']")
            .forEach(button => {

                button.style.display = "none";

            });

        queue.forEach(item => {

            const cancelButton =
                document.getElementById(
                    "cancel-" + item.id
                );

            if (cancelButton) {

                cancelButton.style.display =
                    "inline-block";

            }

            const downloadButton =
                document.getElementById(
                    "download-" + item.id
                );

            if (downloadButton) {

                downloadButton.disabled = true;

                downloadButton.innerText =
                    "En cola";

            }

        });

    } catch (error) {

        console.log(
            "Error actualizando cola:",
            error
        );
    }
}


setInterval(
    updateQueue,
    1500
);

updateQueue();


function previousPage() {

    if (currentPage > 1) {

        currentPage--;

        renderPage();

        window.scrollTo({
            top: 0,
            behavior: "smooth"
        });
    }
}


function nextPage() {

    const totalPages =
        Math.ceil(
            allResults.length /
            resultsPerPage
        );

    if (currentPage < totalPages) {

        currentPage++;

        renderPage();

        window.scrollTo({
            top: 0,
            behavior: "smooth"
        });
    }
}


async function cancelDownloadByVideo(videoId) {

    try {

        const response =
            await fetch("/api/queue");

        const data =
            await response.json();

        const item =
            (data.queue || []).find(
                x => x.id === videoId
            );

        if (!item) {

            alert(
                "No hay ninguna descarga activa para esta canción."
            );

            return;
        }

        if (!confirm(
            "¿Cancelar esta descarga?"
        )) {
            return;
        }

        const cancel =
            await fetch(
                "/api/cancel?id=" +
                encodeURIComponent(item.job)
            );

        const result =
            await cancel.json();

        if (result.error) {

            alert(result.error);

            return;
        }

        updateQueue();

        const cancelButton =
            document.getElementById(
                "cancel-" + videoId
            );

        if (cancelButton) {

            cancelButton.style.display =
                "none";
        }

        const downloadButton =
            document.getElementById(
                "download-" + videoId
            );

        if (downloadButton) {

            downloadButton.disabled = false;

            downloadButton.innerText =
                "↓ Descargar";
        }

    } catch (error) {

        console.error(
            "Error cancelando:",
            error
        );

        alert(
            "Error al cancelar la descarga."
        );
    }
}


async function downloadMusic(id, button) {

    button.disabled = true;

    button.innerText =
        "En cola";

    const status =
        document.getElementById(
            "status-" + id
        );

    status.innerText =
        "🕐 En cola...";

    const result =
        button.closest(".result");

    const progressBox =
        result.querySelector(".result-progress");

    const progressFill =
        result.querySelector(".result-progress-fill");

    const progressText =
        result.querySelector(".result-progress-text");

    if (progressBox) {
        progressBox.style.display = "flex";
    }

    if (progressFill) {
        progressFill.style.width = "0%";
    }

    if (progressText) {
        progressText.innerText = "0%";
    }

    try {

        const response =
            await fetch(
                "/api/download",
                {
                    method: "POST",

                    headers: {
                        "Content-Type":
                            "application/json"
                    },

                    body:
                        JSON.stringify({
                            id: id,
                            title:
                                button
                                .closest(".result")
                                .querySelector(".title")
                                .innerText
                        })
                }
            );

        const data =
            await response.json();

        if (data.error) {

            status.innerHTML =
                "<span class='error'>" +
                escapeHtml(data.error) +
                "</span>";

            button.disabled = false;

            button.innerText =
                "↻ Reintentar";

            return;
        }

        status.innerText =
            "↓ Descargando...";

        checkStatus(
            data.job,
            button,
            status
        );

        updateQueue();

    } catch (error) {

        status.innerHTML =
            "<span class='error'>" +
            "Error iniciando descarga." +
            "</span>";

        button.disabled = false;

        button.innerText =
            "↻ Reintentar";
    }
}


async function checkStatus(
    job,
    button,
    status
) {

    const result =
        button.closest(".result");

    const progressBox =
        result
            ? result.querySelector(".result-progress")
            : null;

    const progressFill =
        result
            ? result.querySelector(".result-progress-fill")
            : null;

    const progressText =
        result
            ? result.querySelector(".result-progress-text")
            : null;

    if (progressBox) {
        progressBox.style.display = "flex";
    }

    const timer =
        setInterval(
            async () => {

                try {

                    const response =
                        await fetch(
                            "/api/status?id=" +
                            encodeURIComponent(job)
                        );

                    const data =
                        await response.json();

                    const progress =
                        Math.max(
                            0,
                            Math.min(
                                100,
                                Number(
                                    data.progress || 0
                                )
                            )
                        );

                    if (progressFill) {
                        progressFill.style.width =
                            progress + "%";

                        progressFill.classList.remove(
                            "result-progress-done"
                        );
                    }

                    if (progressText) {
                        progressText.innerText =
                            progress + "%";
                    }


                    if (
                        data.status ===
                        "queued"
                    ) {

                        status.innerText =
                            "🕐 Procesando cola...";

                        return;
                    }


                    if (
                        data.status ===
                        "running"
                    ) {

                        const message =
                            data.message || "";

                        if (
                            message.includes(
                                "Procesando etiquetas"
                            )
                        ) {

                            status.innerText =
                                "🖼️ Procesando etiquetas y portada...";

                        } else if (
                            message.includes(
                                "Descargando"
                            )
                        ) {

                            status.innerText =
                                "⬇️ " +
                                message;

                        } else {

                            status.innerText =
                                "⬇️ Descargando... " +
                                progress +
                                "%";
                        }

                        return;
                    }


                    if (
                        data.status ===
                        "done"
                    ) {

                        clearInterval(timer);

                        if (progressFill) {
                            progressFill.style.width =
                                "100%";

                            progressFill.classList.add(
                                "result-progress-done"
                            );
                        }

                        if (progressText) {
                            progressText.innerText =
                                "100%";
                        }

                        status.innerHTML =
                            "<span class='success'>" +
                            "✓ Completado · enviado a Navidrome" +
                            "</span>";

                        button.innerText =
                            "✓ Completado";

                        button.disabled =
                            true;

                        showNotice(
                            "✓ Añadido a la biblioteca de Navidrome"
                        );

                        return;
                    }


                    if (
                        data.status ===
                        "error"
                    ) {

                        clearInterval(timer);

                        status.innerHTML =
                            "<span class='error'>" +
                            "✕ " +
                            escapeHtml(
                                data.message ||
                                "Error en la descarga"
                            ) +
                            "</span>";

                        button.disabled =
                            false;

                        button.innerText =
                            "↻ Reintentar";

                        return;
                    }


                    if (
                        data.status ===
                        "cancelled"
                    ) {

                        clearInterval(timer);

                        status.innerText =
                            "⛔ Descarga cancelada";

                        button.disabled =
                            false;

                        button.innerText =
                            "↻ Reintentar";

                        return;
                    }

                } catch (error) {

                    console.log(error);
                }

            },
            1000
        );
}

function showNotice(message) {

    const notice =
        document.getElementById(
            "notice"
        );

    notice.innerText =
        message;

    notice.classList.add("show");

    setTimeout(
        () => {

            notice.classList.remove("show");

        },
        5000
    );
}


function formatDuration(seconds) {

    if (!seconds) {
        return "--:--";
    }

    seconds =
        Math.floor(
            Number(seconds)
        );

    const minutes =
        Math.floor(
            seconds / 60
        );

    const secs =
        seconds % 60;

    return (
        minutes +
        ":" +
        String(secs).padStart(2, "0")
    );
}


function escapeHtml(text) {

    const div =
        document.createElement(
            "div"
        );

    div.textContent =
        text || "";

    return div.innerHTML;
}


function escapeAttribute(text) {

    return String(text || "")
        .replace(/&/g, "&amp;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
}


document
    .getElementById("search")
    .addEventListener(
        "keydown",
        function(e) {

            if (e.key === "Enter") {

                searchMusic();

            }

        }
    );

</script>


<!-- ALBUM_SEARCH_SECTION_V2 -->

<style>
.album-search-panel {
    margin: 20px 0;
    padding: 18px;
    border-radius: 16px;
    background: rgba(255,255,255,.045);
    border: 1px solid rgba(255,255,255,.10);
    box-shadow: 0 8px 24px rgba(0,0,0,.16);
}

.album-search-header {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 14px;
}

.album-search-icon {
    font-size: 25px;
}

.album-search-title {
    font-size: 20px;
    font-weight: 700;
}

.album-search-subtitle {
    opacity: .65;
    font-size: 13px;
    margin-top: 2px;
}

.album-search-row {
    display: flex;
    gap: 8px;
}

.album-search-input {
    flex: 1;
    min-width: 0;
    padding: 11px 14px;
    border-radius: 10px;
    border: 1px solid rgba(255,255,255,.12);
    background: rgba(0,0,0,.22);
    color: inherit;
    font-size: 15px;
    outline: none;
}

.album-search-input:focus {
    border-color: rgba(255,255,255,.30);
}

.album-search-button {
    padding: 11px 17px;
    border: 0;
    border-radius: 10px;
    background: linear-gradient(135deg,#7c3aed,#4f46e5);
    color: white;
    font-weight: 700;
    cursor: pointer;
    white-space: nowrap;
}

.album-search-button:hover {
    filter: brightness(1.12);
}

.album-search-results {
    margin-top: 18px;
}

.album-box {
    padding: 14px;
    border-radius: 14px;
    background: rgba(0,0,0,.18);
    border: 1px solid rgba(255,255,255,.08);
}



.album-stop-all {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    margin: 0 0 12px 6px;
    padding: 8px 14px;
    border: 1px solid rgba(239,68,68,.45);
    border-radius: 9px;
    background: rgba(239,68,68,.12);
    color: inherit;
    font-size: 13px;
    font-weight: 700;
    cursor: pointer;
    transition:
        background .2s ease,
        border-color .2s ease,
        transform .15s ease;
}

.album-stop-all:hover {
    background: rgba(239,68,68,.24);
    border-color: rgba(239,68,68,.70);
}

.album-stop-all:active {
    transform: scale(.97);
}

.album-download-all {
    display: inline-flex;
    align-items: center;
    justify-content: center;

    margin: 0 0 12px 0;
    padding: 8px 14px;

    border: 1px solid rgba(124,58,237,.45);
    border-radius: 9px;

    background: rgba(124,58,237,.16);
    color: inherit;

    font-size: 13px;
    font-weight: 700;

    cursor: pointer;

    transition:
        background .2s ease,
        border-color .2s ease,
        transform .15s ease;
}

.album-download-all:hover {
    background: rgba(124,58,237,.28);
    border-color: rgba(124,58,237,.70);
}

.album-download-all:active {
    transform: scale(.97);
}

.album-download-all:disabled {
    opacity: .55;
    cursor: default;
    transform: none;
}

.album-back-button {
    display: inline-flex !important;
    align-items: center !important;
    gap: 9px !important;

    margin: 0 0 18px 0 !important;
    padding: 10px 15px !important;

    border: 1px solid rgba(255,255,255,.10) !important;
    border-radius: 12px !important;

    background: rgba(255,255,255,.045) !important;
    color: rgba(255,255,255,.78) !important;

    font-size: 13px !important;
    font-weight: 700 !important;

    cursor: pointer !important;

    box-shadow: 0 5px 18px rgba(0,0,0,.12) !important;

    transition:
        background .18s ease,
        border-color .18s ease,
        color .18s ease,
        transform .18s ease !important;
}

.album-back-button:hover {
    background: rgba(124,92,255,.13) !important;
    border-color: rgba(124,92,255,.32) !important;
    color: #ffffff !important;
    transform: translateX(-3px) !important;
}

.album-back-button:active {
    transform: translateX(-1px) scale(.98) !important;
}

.album-box-title {
    font-size: 21px;
    font-weight: 800;
    margin-bottom: 3px;
}

.album-box-artist {
    opacity: .7;
    margin-bottom: 10px;
    font-size: 14px;
}

/* =========================================================
   LISTA DE CANCIONES COMPACTA
========================================================= */

.album-track {
    display: flex;
    align-items: center;

    gap: 7px;

    min-height: 52px;
    padding: 4px 0;

    border-top: 1px solid rgba(255,255,255,.07);

    box-sizing: border-box;
}

.album-track-number {
    width: 20px;
    flex: 0 0 20px;

    text-align: center;

    font-size: 11px;
    opacity: .45;
}

.album-track-thumb {
    width: 60px;
    height: 60px;

    flex: 0 0 60px;

    object-fit: cover;

    border-radius: 6px;

    background: rgba(255,255,255,.05);
}

.album-track-name {
    flex: 1;
    min-width: 0;

    line-height: 1.15;
}

.album-track-name strong {
    display: inline-block;

    max-width: 100%;

    font-size: 15px;
    font-weight: 600;

    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;

    vertical-align: middle;
}

.album-track-duration {
    font-size: 12px;

    margin-left: 5px !important;

    opacity: .5;
}

.album-track-status {
    display: block;

    margin-top: 2px !important;

    font-size: 11px;
    line-height: 1.1;

    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;

    opacity: .45;
}

.album-track-buttons {
    display: flex;
    align-items: center;

    gap: 4px;

    flex: 0 0 auto;
}

.album-track-buttons button {
    min-height: 30px;

    border: 0;
    border-radius: 6px;

    padding: 4px 7px;

    cursor: pointer;

    color: white;
    background: rgba(255,255,255,.10);

    font-size: 13px;
}

.album-track-buttons .album-download {
    background: rgba(124,58,237,.75);
}


/* =========================================================
   PROGRESO
========================================================= */

.album-track-progress {
    width: 100%;

    display: flex;
    align-items: center;

    gap: 5px;

    margin-top: 3px;
}

.album-track-progress-bar {
    flex: 1;
    min-width: 0;

    height: 3px;

    overflow: hidden;

    border-radius: 99px;

    background: rgba(255,255,255,.09);
}

.album-track-progress-fill {
    height: 100%;
    width: 0%;

    border-radius: 99px;

    background: rgba(124,58,237,.95);

    transition: width .4s ease;
}

.album-track-progress-text {
    min-width: 27px;

    text-align: right;

    font-size: 8px;

    opacity: .6;
}

.album-track-progress-fill.album-progress-done {
    background: #22c55e;
}

.album-track-status.album-status-done {
    color: #22c55e !important;
    opacity: 1 !important;
    font-weight: 600;
}


/* =========================================================
   MÓVIL
========================================================= */

@media (max-width: 600px) {

    .album-search-row {
        flex-direction: column;
    }

    .album-search-button {
        width: 100%;
    }

    .album-track {
        gap: 5px;

        min-height: 47px;

        padding: 3px 0;
    }

    .album-track-number {
        width: 18px;
        flex-basis: 18px;

        font-size: 10px;
    }

    .album-track-thumb {
        width: 52px;
        height: 52px;

        flex-basis: 52px;

        border-radius: 5px;
    }

    .album-track-name strong {
        font-size: 14px;
    }

    .album-track-duration {
        font-size: 11px;

        margin-left: 4px !important;
    }

    .album-track-status {
        font-size: 10px;

        margin-top: 2px !important;
    }

    .album-track-buttons {
        gap: 3px;
    }

    .album-track-buttons button {
        min-height: 26px;

        padding: 3px 5px;

        font-size: 12px;
    }

    .album-track-progress {
        margin-top: 2px;
    }

    .album-track-progress-bar {
        height: 3px;
    }

    .album-track-progress-text {
        min-width: 25px;

        font-size: 8px;
    }
}


/* =========================================================
   MÓVIL MUY ESTRECHO
========================================================= */

@media (max-width: 400px) {

    .album-track {
        gap: 4px;
    }

    .album-track-number {
        width: 17px;
        flex-basis: 17px;

        font-size: 9px;
    }

    .album-track-thumb {
        width: 48px;
        height: 48px;

        flex-basis: 48px;
    }

    .album-track-name strong {
        font-size: 13px;
    }

    .album-track-buttons button {
        min-height: 27px;

        padding: 3px 4px;

        font-size: 11px;
    }
}

</style>

<script>
(function() {

    function createAlbumSearchPanel() {

        if (document.getElementById("albumSearchPanel")) {
            return;
        }

        /*
         * Buscamos la caja de búsqueda actual.
         * No dependemos de una estructura concreta del HTML.
         */
        const inputs = document.querySelectorAll("input");

        let searchInput = null;

        for (const input of inputs) {
            const ph = (input.placeholder || "").toLowerCase();

            if (
                ph.includes("buscar") ||
                ph.includes("cancion") ||
                ph.includes("música") ||
                ph.includes("musica") ||
                input.id.toLowerCase().includes("search")
            ) {
                searchInput = input;
                break;
            }
        }

        if (!searchInput) {
            console.log("No se encontró la caja de búsqueda principal.");
            return;
        }

        const panel = document.createElement("section");

        panel.id = "albumSearchPanel";
        panel.className = "album-search-panel";

        panel.innerHTML = `
            <div class="album-search-header">
                <div class="album-search-icon">💿</div>
                <div>
                    <div class="album-search-title">Buscar álbum</div>
                    <div class="album-search-subtitle">
                        Busca un álbum completo y muestra todas sus canciones
                    </div>
                </div>
            </div>

            <div class="album-search-row">
                <input
                    id="albumSearchInput"
                    class="album-search-input"
                    type="text"
                    placeholder="Escribe artista o álbum..."
                    autocomplete="off"
                >

                <button
                    id="albumSearchButton"
                    class="album-search-button"
                    type="button">
                    🔎 Buscar álbum
                </button>
            </div>

            <div id="albumSearchResults" class="album-search-results"></div>
        `;

        /*
         * Insertar justo después del bloque de búsqueda principal.
         */
        let container = searchInput.closest(".search-box");

        if (!container) {
            container = searchInput.closest(".search");
        }

        if (!container) {
            container = searchInput.parentElement;
        }

        if (container && container.parentElement) {
            container.parentElement.insertBefore(panel, container.nextSibling);
        } else {
            document.body.appendChild(panel);
        }

        const albumInput = document.getElementById("albumSearchInput");
        const albumButton = document.getElementById("albumSearchButton");

        albumButton.addEventListener("click", searchAlbum);

        albumInput.addEventListener("keydown", function(e) {
            if (e.key === "Enter") {
                searchAlbum();
            }
        });
    }


    async function searchAlbum() {

        const input =
            document.getElementById("albumSearchInput");

        const results =
            document.getElementById("albumSearchResults");

        if (!input || !results) return;

        const query =
            input.value.trim();

        if (!query) {

            results.innerHTML =
                '<div class="album-error">' +
                'Escribe el nombre de un álbum o artista.' +
                '</div>';

            return;
        }

        results.innerHTML =
            '<div class="album-loading">' +
            '💿 Buscando...' +
            '</div>';

        try {

            const response = await fetch(
                "/api/album?q=" +
                encodeURIComponent(query)
            );

            const data =
                await response.json();

            if (!response.ok) {

                throw new Error(
                    data.error ||
                    "Error buscando álbum"
                );
            }

            /*
             * Búsqueda de artista:
             * el backend devuelve { albums: [...] }
             */
            if (
                data &&
                Array.isArray(data.albums)
            ) {

                if (!data.albums.length) {

                    results.innerHTML =
                        '<div class="album-error">' +
                        'No se encontraron álbumes.' +
                        '</div>';

                    return;
                }

                renderAlbumList(
                    data.albums,
                    data.artist || query
                );

                return;
            }

            /*
             * Búsqueda de álbum concreto:
             * mantenemos el comportamiento anterior.
             */
            const album =
                Array.isArray(data)
                    ? data[0]
                    : data;

            if (
                !album ||
                !album.tracks ||
                !album.tracks.length
            ) {

                results.innerHTML =
                    '<div class="album-error">' +
                    'No se encontró ningún álbum.' +
                    '</div>';

                return;
            }

            renderAlbum(album);

        } catch (error) {

            console.error(error);

            results.innerHTML =
                '<div class="album-error">' +
                escapeAlbum(
                    error.message ||
                    "No se pudo buscar el álbum."
                ) +
                '</div>';
        }
    }


    function renderAlbumList(albums, artist) {

        const results =
            document.getElementById(
                "albumSearchResults"
            );

        if (!results) return;

        // Guardamos una copia de la lista original para el botón Atrás.
        window.currentAlbumList = Array.isArray(albums)
            ? albums.slice()
            : [];

        window.currentAlbumArtist = artist || "";

        let html = `
            <div class="album-box">

                <div class="album-box-title">
                    💿 Álbumes de
                    ${escapeAlbum(artist)}
                </div>

                <div class="album-box-artist">
                    ${albums.length} álbumes
                </div>

                <div class="album-list">
        `;

        albums.forEach(function(album, index) {

            const title =
                album.title ||
                "Álbum";

            const date =
                album.date ||
                "";

            html += `
                <button
                    type="button"
                    class="album-select"
                    data-album-index="${index}">

                    <span class="album-select-number">
                        ${index + 1}
                    </span>

                    <span class="album-select-info">

                        <strong>
                            ${escapeAlbum(title)}
                        </strong>

                        ${
                            date
                                ? `<small>${escapeAlbum(date)}</small>`
                                : ""
                        }

                    </span>

                    <span class="album-select-arrow">
                        ›
                    </span>

                </button>
            `;
        });

        html += `
                </div>
            </div>
        `;

        results.innerHTML = html;

        // Guardamos el selector actual para poder volver desde un álbum.
        window.previousAlbumListHTML = results.innerHTML;

        const buttons =
            results.querySelectorAll(
                ".album-select"
            );

        buttons.forEach(function(button, index) {

            button.addEventListener(
                "click",
                function() {

                    const album =
                        albums[index];

                    if (!album || !album.id) {
                        return;
                    }

                    loadAlbumById(
                        album.id,
                        album.title,
                        album.artist || artist
                    );
                }
            );
        });
    }


    window.backToAlbumList = function() {

        const results =
            document.getElementById(
                "albumSearchResults"
            );

        if (!results) return;

        // Detener reproducción del álbum si está activa.
        if (
            typeof albumPlayerState !== "undefined" &&
            albumPlayerState.active
        ) {
            stopAlbumPlayback();
        }

        // Volver directamente al selector de álbumes.
        if (
            window.currentAlbumList &&
            Array.isArray(window.currentAlbumList)
        ) {
            renderAlbumList(
                window.currentAlbumList,
                window.currentAlbumArtist || ""
            );

            return;
        }

        // Último recurso: repetir la búsqueda.
        const input =
            document.getElementById(
                "albumSearchInput"
            );

        if (input && input.value.trim()) {
            searchAlbum();
        }
    };

    async function loadAlbumById(
        releaseGroupId,
        albumTitle,
        artist
    ) {

        const results =
            document.getElementById(
                "albumSearchResults"
            );

        if (!results) return;

        results.innerHTML =
            '<div class="album-loading">' +
            '💿 Cargando ' +
            escapeAlbum(albumTitle) +
            '...' +
            '</div>';

        try {

            const response = await fetch(
                "/api/album?id=" +
                encodeURIComponent(releaseGroupId)
            );

            const data =
                await response.json();

            if (!response.ok) {

                throw new Error(
                    data.error ||
                    "No se pudo cargar el álbum."
                );
            }

            if (
                !data ||
                !data.tracks ||
                !data.tracks.length
            ) {

                results.innerHTML =
                    '<div class="album-error">' +
                    'El álbum no tiene canciones disponibles.' +
                    '</div>';

                return;
            }

            renderAlbum(data);

        } catch (error) {

            console.error(error);

            results.innerHTML =
                '<div class="album-error">' +
                escapeAlbum(
                    error.message ||
                    "No se pudo cargar el álbum."
                ) +
                '</div>';
        }
    }


    async function findAlbumTrack(track, artist, albumTitle, row) {

        try {

            const query =
                (artist ? artist + " " : "") +
                (track.title || "");

            const response = await fetch(
                "/api/album-track?q=" +
                encodeURIComponent(query)
            );

            const data = await response.json();

            if (!response.ok || !data || !data.id) {
                row.querySelector(".album-track-status").textContent =
                    "No encontrado";
                return;
            }

            track.id = data.id;
            track.video_id = data.id;

            if (data.title) {
                track.youtube_title = data.title;
            }

            if (data.thumbnail) {
                track.thumbnail = data.thumbnail;
            }

            if (data.duration) {
                track.duration = data.duration;
            }

            if (data.channel) {
                track.channel = data.channel;
            }

            const buttons = row.querySelector(".album-track-buttons");
            const status = row.querySelector(".album-track-status");

            /*
             * No eliminamos el estado.
             * Lo necesitamos para mostrar:
             * En cola → Descargando → Procesando → Completado.
             */
            if (status) {
                status.textContent =
                    "✓ Canción encontrada";
            }

            if (!buttons) return;

            buttons.innerHTML = `
                <button
                    type="button"
                    class="btn preview"
                    onclick="previewMusic('${escapeAlbumAttr(track.id)}', this)"
                    title="Reproducir">
                    ▶
                </button>

                <button
                    type="button"
                    class="album-download"
                    onclick="albumDownload('${escapeAlbumAttr(track.id)}','${escapeAlbumAttr(track.title)}')"
                    title="Descargar">
                    Descargar
                </button>
            `;

            const durationElement =
                row.querySelector(".album-track-duration");

            if (durationElement && track.duration) {
                durationElement.textContent =
                    formatAlbumDuration(track.duration);
            }

            const thumb = row.querySelector(".album-track-thumb");

            if (thumb && track.thumbnail) {
                thumb.src = track.thumbnail;
                thumb.style.display = "block";
            }

        } catch (error) {

            console.error(
                "Error buscando canción:",
                track.title,
                error
            );

            const status = row.querySelector(".album-track-status");

            if (status) {
                status.textContent = "Error";
            }
        }
    }


    function renderAlbum(album) {

        const results =
            document.getElementById("albumSearchResults");

        if (!results) return;

        const title =
            album.title ||
            album.album ||
            "Álbum";

        const artist =
            album.artist ||
            album.album_artist ||
            album.channel ||
            "";

        const tracks =
            album.tracks ||
            album.songs ||
            [];

        /*
         * Guardamos el álbum actual para poder descargarlo completo.
         */
        window.currentAlbumTracks = tracks;
        window.currentAlbumTitle = title;

        let html = `
            <div class="album-box">

                <button
                    type="button"
                    class="album-back-button"
                    onclick="return window.backToAlbumList()">
                    ← Volver a álbumes
                </button>

                <div class="album-box-title">
                    💿 ${escapeAlbum(title)}
                </div>

                <div class="album-box-artist">
                    ${escapeAlbum(artist)}
                    ${tracks.length
                        ? " · " + tracks.length + " canciones"
                        : ""}
                </div>

                ${
                    tracks.length
                        ? `
                        <button
                            type="button"
                            class="album-album-play"
                            onclick="playCompleteAlbum()">
                            ▶▶ Escuchar álbum
                        </button>

                        <button
                            type="button"
                            class="album-download-all"
                            onclick="albumDownloadAll()">
                            ⬇ Álbum completo
                        </button>

                        <button
                            type="button"
                            class="album-stop-all"
                            onclick="window.albumStopAll()">
                            ⏹ Parar álbum
                        </button>
                        `
                        : ""
                }

                <div class="album-track-list">
        `;

        if (!tracks.length) {

            html += `
                <div class="album-error">
                    El álbum se encontró, pero no se recibieron canciones.
                </div>
            `;

        } else {

            tracks.forEach(function(track, index) {

                const name =
                    track.title ||
                    track.name ||
                    "Canción";

                const duration =
                    track.duration
                    ? formatAlbumDuration(track.duration)
                    : "";

                html += `
                    <div
                        class="album-track result"
                        data-track-index="${index}">

                        <div class="album-track-number">
                            ${index + 1}
                        </div>

                        <img
                            class="album-track-thumb"
                            src="${escapeAlbumAttr(track.thumbnail || "")}"
                            style="${track.thumbnail
                                ? ""
                                : "display:none;"}"
                            loading="lazy">

                        <div class="album-track-name">

                            <strong>
                                ${escapeAlbum(name)}
                            </strong>

                            <small
                                class="album-track-duration"
                                style="opacity:.55;margin-left:8px">
                                ${duration}
                            </small>

                            <small
                                class="album-track-status"
                                style="display:block;opacity:.45;margin-top:4px">
                                🔎 Buscando...
                            </small>

                            <div
                                class="album-track-progress"
                                style="display:none;">

                                <div class="album-track-progress-bar">
                                    <div
                                        class="album-track-progress-fill"
                                        style="width:0%">
                                    </div>
                                </div>

                                <small class="album-track-progress-text">
                                    0%
                                </small>

                            </div>

                        </div>

                        <div class="album-track-buttons">
                        </div>

                    </div>
                `;
            });
        }

        html += `
                </div>
            </div>
        `;

        results.innerHTML = html;

        /*
         * Una vez pintado el álbum, buscamos cada canción
         * individualmente en YouTube.
         */
        if (tracks.length) {

            const rows =
                results.querySelectorAll(".album-track");

            tracks.forEach(function(track, index) {

                const row = rows[index];

                if (!row) return;

                findAlbumTrack(
                    track,
                    artist,
                    title,
                    row
                );
            });

            startAlbumProgressMonitor();
        }
    }



    /*
     * Actualiza el progreso de las canciones del álbum
     * usando la cola real de descargas.
     */
    async function updateAlbumTrackProgress() {

        try {

            const response =
                await fetch("/api/queue");

            const data =
                await response.json();

            const queue =
                data.queue || [];

            const history =
                data.history || [];

            const rows =
                document.querySelectorAll(
                    ".album-track"
                );

            rows.forEach(function(row) {

                const index =
                    parseInt(
                        row.dataset.trackIndex,
                        10
                    );

                const track =
                    window.currentAlbumTracks &&
                    window.currentAlbumTracks[index];

                if (!track || !track.id) {
                    return;
                }

                /*
                 * Primero buscamos la canción en la cola.
                 */
                let item =
                    queue.find(function(item) {
                        return item.id === track.id;
                    });

                /*
                 * Si ya terminó, puede estar en history.
                 */
                if (!item) {

                    item =
                        history
                            .slice()
                            .reverse()
                            .find(function(item) {
                                return item.id === track.id;
                            });
                }

                const progressBox =
                    row.querySelector(
                        ".album-track-progress"
                    );

                const progressFill =
                    row.querySelector(
                        ".album-track-progress-fill"
                    );

                const progressText =
                    row.querySelector(
                        ".album-track-progress-text"
                    );

                const status =
                    row.querySelector(
                        ".album-track-status"
                    );

                if (!progressBox) {
                    return;
                }

                if (!item) {
                    return;
                }

                const progress =
                    Math.max(
                        0,
                        Math.min(
                            100,
                            Number(item.progress || 0)
                        )
                    );

                /*
                 * DESCARGA EN COLA
                 */
                if (item.status === "queued") {

                    progressBox.style.display = "flex";

                    if (progressFill) {
                        progressFill.style.width =
                            "0%";

                        progressFill.classList.remove(
                            "album-progress-done"
                        );
                    }

                    if (progressText) {
                        progressText.textContent =
                            "0%";
                    }

                    if (status) {
                        status.textContent =
                            "🕐 Procesando cola...";
                    }

                    return;
                }

                /*
                 * DESCARGANDO
                 */
                if (item.status === "running") {

                    progressBox.style.display = "flex";

                    if (progressFill) {
                        progressFill.style.width =
                            progress + "%";

                        progressFill.classList.remove(
                            "album-progress-done"
                        );
                    }

                    if (progressText) {
                        progressText.textContent =
                            progress + "%";
                    }

                    if (status) {

                        const message =
                            item.message || "";

                        if (
                            message.includes(
                                "Procesando etiquetas"
                            )
                        ) {

                            status.textContent =
                                "🖼️ Procesando etiquetas y portada...";

                        } else if (
                            message.includes(
                                "Descargando"
                            )
                        ) {

                            status.textContent =
                                "⬇️ " + message;

                        } else {

                            status.textContent =
                                "⬇️ " +
                                (
                                    message ||
                                    "Descargando..."
                                );
                        }
                    }

                    return;
                }

                /*
                 * TERMINADO
                 */
                if (item.status === "done") {

                    progressBox.style.display = "flex";

                    if (progressFill) {
                        progressFill.style.width =
                            "100%";

                        progressFill.classList.add(
                            "album-progress-done"
                        );
                    }

                    if (progressText) {
                        progressText.textContent =
                            "100%";
                    }

                    if (status) {
                        status.textContent =
                            "✅ Completado · enviado a Navidrome";

                        status.classList.add(
                            "album-status-done"
                        );
                    }

                    return;
                }

                /*
                 * ERROR
                 */
                if (item.status === "error") {

                    progressBox.style.display = "flex";

                    if (progressFill) {
                        progressFill.style.width =
                            progress + "%";

                        progressFill.classList.remove(
                            "album-progress-done"
                        );
                    }

                    if (progressText) {
                        progressText.textContent =
                            progress + "%";
                    }

                    if (status) {
                        status.textContent =
                            "❌ " +
                            (
                                item.message ||
                                "Error en la descarga"
                            );

                        status.classList.remove(
                            "album-status-done"
                        );
                    }

                    return;
                }

                /*
                 * CANCELADA
                 */
                if (item.status === "cancelled") {

                    if (status) {
                        status.textContent =
                            "⛔ Descarga cancelada";
                    }

                    return;
                }

            });

        } catch (error) {

            console.error(
                "Error actualizando progreso del álbum:",
                error
            );
        }
    }


    window.albumProgressTimer =
        window.albumProgressTimer || null;


    function startAlbumProgressMonitor() {

        if (window.albumProgressTimer) {
            clearInterval(
                window.albumProgressTimer
            );
        }

        updateAlbumTrackProgress();

        window.albumProgressTimer =
            setInterval(
                updateAlbumTrackProgress,
                1000
            );
    }


    
window.albumStopAll = async function() {

    const tracks =
        window.currentAlbumTracks || [];

    if (!tracks.length) {
        return;
    }

    if (!confirm(
        "¿Parar todas las descargas activas de este álbum?"
    )) {
        return;
    }

    try {

        const response =
            await fetch("/api/queue");

        const data =
            await response.json();

        const albumIds =
            new Set(
                tracks
                    .filter(track => track.id)
                    .map(track => track.id)
            );

        const active =
            (data.queue || []).filter(item =>
                albumIds.has(item.id)
            );

        if (!active.length) {
            alert(
                "No hay descargas activas de este álbum."
            );
            return;
        }

        let cancelled = 0;

        for (const item of active) {

            if (!item.job) {
                continue;
            }

            try {

                const cancel =
                    await fetch(
                        "/api/cancel?id=" +
                        encodeURIComponent(item.job)
                    );

                const result =
                    await cancel.json();

                if (!result.error) {
                    cancelled++;
                }

            } catch (error) {

                console.error(
                    "Error cancelando:",
                    item.id,
                    error
                );
            }
        }

        if (
            typeof updateQueue === "function"
        ) {
            updateQueue();
        }

        if (
            typeof startAlbumProgressMonitor === "function"
        ) {
            startAlbumProgressMonitor();
        }

        alert(
            cancelled +
            " descargas del álbum detenidas."
        );

    } catch (error) {

        console.error(
            "Error obteniendo la cola:",
            error
        );

        alert(
            "No se pudo detener el álbum."
        );
    }
};

window.albumDownloadAll = async function() {

        const tracks =
            window.currentAlbumTracks || [];

        if (!tracks.length) {

            alert(
                "No hay canciones para descargar."
            );

            return;
        }

        const button =
            document.querySelector(
                ".album-download-all"
            );

        /*
         * Comprobamos si todavía quedan canciones
         * buscando en YouTube.
         */
        const pending =
            tracks.filter(function(track) {
                return !track.id;
            });

        if (pending.length) {

            alert(
                "Espera a que termine la búsqueda de las canciones.\n\n" +
                "Faltan " +
                pending.length +
                " por encontrar."
            );

            return;
        }

        const validTracks =
            tracks.filter(function(track) {
                return track.id;
            });

        if (!validTracks.length) {

            alert(
                "No se encontraron canciones descargables."
            );

            return;
        }

        if (button) {

            button.disabled = true;

            button.innerText =
                "⏳ Añadiendo álbum...";
        }

        let added = 0;

        for (const track of validTracks) {

            try {

                const response =
                    await fetch(
                        "/api/download",
                        {
                            method: "POST",
                            headers: {
                                "Content-Type": "application/json"
                            },
                            body: JSON.stringify({
                                id: track.id,
                                title:
                                    track.title ||
                                    "Canción"
                            })
                        }
                    );

                const data =
                    await response.json();

                if (response.ok) {

                    added++;

                } else {

                    console.error(
                        "No se pudo añadir:",
                        track.title,
                        data.error
                    );
                }

            } catch (error) {

                console.error(
                    "Error añadiendo:",
                    track.title,
                    error
                );
            }
        }

        if (
            typeof updateQueue === "function"
        ) {

            updateQueue();
        }

        if (button) {

            button.disabled = false;

            button.innerText =
                "⬇ Álbum completo";
        }

        alert(
            added +
            " canciones añadidas a la cola."
        );
    };


    window.albumPreview = async function(id) {

        try {

            if (window.albumAudio) {
                window.albumAudio.pause();
                window.albumAudio = null;
            }

            const response = await fetch(
                "/api/preview?id=" +
                encodeURIComponent(id)
            );

            const data = await response.json();

            if (!response.ok || !data.url) {
                console.error(
                    "Error reproduciendo álbum:",
                    data.error || "Sin URL de audio"
                );
                return;
            }

            const audio = new Audio(data.url);

            window.albumAudio = audio;

            audio.volume = 1;

            audio.play().catch(function(error) {
                console.error(
                    "No se pudo reproducir:",
                    error
                );
            });

            audio.onended = function() {
                if (window.albumAudio === audio) {
                    window.albumAudio = null;
                }
            };

        } catch (error) {

            console.error(
                "Error en reproducción del álbum:",
                error
            );
        }
    };


    window.albumDownload = async function(id, title) {

        try {

            const response = await fetch(
                "/api/download",
                {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify({
                        id: id,
                        title: title
                    })
                }
            );

            const data = await response.json();

            if (!response.ok) {

                alert(
                    data.error ||
                    "No se pudo añadir la canción"
                );

                return;
            }

            if (typeof updateQueue === "function") {
                updateQueue();
            }

        } catch (e) {

            console.error(e);

            alert(
                "Error al añadir la canción"
            );
        }
    };


    function formatAlbumDuration(seconds) {

        seconds = parseInt(seconds || 0);

        if (!seconds) return "";

        const min = Math.floor(seconds / 60);
        const sec = String(seconds % 60).padStart(2, "0");

        return min + ":" + sec;
    }


    function escapeAlbum(value) {

        return String(value || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }


    function escapeAlbumAttr(value) {

        return String(value || "")
            .replace(/\\/g, "\\\\")
            .replace(/'/g, "\\'")
            .replace(/"/g, "&quot;");
    }


    /*
     * Esperamos a que el HTML original termine de cargarse.
     */
    if (document.readyState === "loading") {

        document.addEventListener(
            "DOMContentLoaded",
            createAlbumSearchPanel
        );

    } else {

        createAlbumSearchPanel();

    }

})();
</script>


<script>

let spotifyPlaylist = null;


function spotifyEscape(value) {

    return String(value || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}


function spotifyFormatDuration(value) {

    let ms = parseInt(value || 0);

    if (!ms) {
        return "";
    }

    if (ms > 100000) {
        ms = Math.round(ms / 1000) * 1000;
    }

    const seconds = Math.floor(ms / 1000);

    const min = Math.floor(seconds / 60);

    const sec = String(
        seconds % 60
    ).padStart(2, "0");

    return min + ":" + sec;
}



async function loadSpotifySavedLists() {

    const container =
        document.getElementById("spotifySavedLists");

    if (!container) {
        return;
    }

    try {

        const response =
            await fetch(
                "/api/spotify/lists",
                {
                    cache: "no-store"
                }
            );

        const data =
            await response.json();

        if (!response.ok) {
            throw new Error(
                data.error ||
                "No se pudieron cargar las listas."
            );
        }

        const lists = data.lists || [];

        if (!lists.length) {

            container.innerHTML =
                "<div class='spotify-saved-empty'>" +
                "No hay playlists guardadas todavía." +
                "</div>";

            return;
        }

        container.innerHTML = "";

        lists.forEach(function(playlist) {

            const item =
                document.createElement("div");

            item.className =
                "spotify-saved-item";

            const date =
                playlist.created_at
                    ? new Date(
                        playlist.created_at
                    ).toLocaleString("es-ES")
                    : "";

            item.innerHTML = `
                <div class="spotify-saved-info">
                    <div class="spotify-saved-name">
                        🎵 ${spotifyEscape(playlist.name)}
                    </div>
                    <div class="spotify-saved-meta">
                        ${(playlist.count || 0)} canciones
                        ${date ? " • " + spotifyEscape(date) : ""}
                    </div>
                </div>

                <div class="spotify-saved-actions">
                    <button
                        type="button"
                        class="btn preview spotify-load-saved">
                        ▶ Cargar
                    </button>

                    <button
                        type="button"
                        class="btn cancel spotify-delete-saved">
                        🗑️ Eliminar
                    </button>
                </div>
            `;

            const loadButton =
                item.querySelector(
                    ".spotify-load-saved"
                );

            const deleteButton =
                item.querySelector(
                    ".spotify-delete-saved"
                );

            loadButton.addEventListener(
                "click",
                function() {
                    loadSpotifySavedList(
                        playlist.id
                    );
                }
            );

            deleteButton.addEventListener(
                "click",
                function() {
                    deleteSpotifySavedList(
                        playlist.id,
                        playlist.name
                    );
                }
            );

            container.appendChild(item);
        });

    } catch (error) {

        console.error(
            "Error cargando listas Spotify:",
            error
        );

        container.innerHTML =
            "<div class='spotify-saved-empty'>" +
            "❌ No se pudo cargar el historial." +
            "</div>";
    }
}


async function loadSpotifySavedList(id) {

    try {

        const response =
            await fetch(
                "/api/spotify/list/load",
                {
                    method: "POST",
                    headers: {
                        "Content-Type":
                            "application/json"
                    },
                    body: JSON.stringify({
                        id: id
                    })
                }
            );

        const data =
            await response.json();

        if (!response.ok) {
            throw new Error(
                data.error ||
                "No se pudo cargar la playlist."
            );
        }

        spotifyPlaylist =
            data.playlist;

        const info =
            document.getElementById(
                "spotifyInfo"
            );

        const actions =
            document.getElementById(
                "spotifyActions"
            );

        const status =
            document.getElementById(
                "spotifyStatus"
            );

        info.style.display =
            "block";

        info.innerHTML = `
            <strong>
                🎵 ${spotifyEscape(
                    spotifyPlaylist.name
                )}
            </strong>
            ${spotifyPlaylist.count || spotifyPlaylist.tracks.length}
            canciones
        `;

        renderSpotifyPlaylist();

        actions.style.display =
            "flex";

        status.innerText =
            "📚 Playlist cargada desde el historial.";

    } catch (error) {

        alert(
            error.message
        );
    }
}


async function deleteSpotifySavedList(
    id,
    name
) {

    if (
        !confirm(
            '¿Eliminar la playlist "' +
            name +
            '" del historial?'
        )
    ) {
        return;
    }

    try {

        const response =
            await fetch(
                "/api/spotify/list/delete",
                {
                    method: "POST",
                    headers: {
                        "Content-Type":
                            "application/json"
                    },
                    body: JSON.stringify({
                        id: id
                    })
                }
            );

        const data =
            await response.json();

        if (!response.ok) {
            throw new Error(
                data.error ||
                "No se pudo eliminar la playlist."
            );
        }

        loadSpotifySavedLists();

    } catch (error) {

        alert(
            error.message
        );
    }
}


async function importSpotifyFile(input) {

    const file = input.files[0];

    if (!file) {
        return;
    }

    const info =
        document.getElementById("spotifyInfo");

    const list =
        document.getElementById("spotifyList");

    const actions =
        document.getElementById("spotifyActions");

    const status =
        document.getElementById("spotifyStatus");

    info.style.display = "block";

    info.innerHTML =
        "⏳ Leyendo playlist...";

    list.innerHTML = "";

    actions.style.display = "none";

    status.innerText = "";

    try {

        const content =
            await file.text();

        const response =
            await fetch(
                "/api/spotify/import",
                {
                    method: "POST",
                    headers: {
                        "Content-Type":
                            "application/json"
                    },
                    body: JSON.stringify({
                        filename: file.name,
                        content: content
                    })
                }
            );

        const data =
            await response.json();

        if (!response.ok) {

            throw new Error(
                data.error ||
                "No se pudo importar la playlist."
            );
        }

        spotifyPlaylist = data;

        info.innerHTML = `
            <strong>
                🎵 ${spotifyEscape(data.name)}
            </strong>
            ${data.count} canciones
        `;

        renderSpotifyPlaylist();

        actions.style.display =
            "flex";

        status.innerText =
            "Selecciona las canciones que quieras descargar.";

        loadSpotifySavedLists();

    } catch (error) {

        spotifyPlaylist = null;

        info.innerHTML =
            "❌ " +
            spotifyEscape(
                error.message
            );

        list.innerHTML = "";

        actions.style.display =
            "none";

    }

    input.value = "";
}


function renderSpotifyPlaylist() {

    const list =
        document.getElementById(
            "spotifyList"
        );

    if (
        !spotifyPlaylist ||
        !spotifyPlaylist.tracks ||
        !spotifyPlaylist.tracks.length
    ) {

        list.innerHTML =
            "<div class='empty'>No hay canciones.</div>";

        return;
    }

    list.innerHTML = "";

    spotifyPlaylist.tracks.forEach(
        function(track, index) {

            const div =
                document.createElement("div");

            div.className =
                "spotify-track";

            const artist =
                track.artist || "";

            const album =
                track.album || "";

            const metaParts = [];

            if (artist) {
                metaParts.push(artist);
            }

            if (album) {
                metaParts.push(album);
            }

            const duration =
                spotifyFormatDuration(
                    track.duration
                );

            if (duration) {
                metaParts.push(
                    "⏱️ " + duration
                );
            }

            div.innerHTML = `

                <input
                    class="spotify-check"
                    type="checkbox"
                    data-index="${index}"
                    checked
                >

                <div class="spotify-track-info">

                    <div class="spotify-track-title">
                        ${spotifyEscape(track.title)}
                    </div>

                    <div class="spotify-track-meta">
                        ${spotifyEscape(
                            metaParts.join(" • ")
                        )}
                    </div>

                    <div
                        class="spotify-download-status"
                        style="margin-top:10px; font-size:13px; font-weight:700;">
                    </div>

                    <div
                        class="spotify-track-player"
                        style="display:flex; gap:8px; margin-top:8px; flex-wrap:wrap;">

                        <button
                            type="button"
                            class="btn preview spotify-play"
                            data-index="${index}">
                            ▶ Escuchar
                        </button>

                        <button
                            type="button"
                            class="btn cancel spotify-stop"
                            data-index="${index}">
                            ⏹ Parar
                        </button>

                        <button
                            type="button"
                            class="btn download spotify-download"
                            data-index="${index}">
                            ↓ Descargar
                        </button>

                    </div>

                </div>

                <div class="spotify-number">
                    ${index + 1}
                </div>

            `;

            const playButton =
                div.querySelector(".spotify-play");

            const stopButton =
                div.querySelector(".spotify-stop");

            const downloadButton =
                div.querySelector(".spotify-download");

            playButton.addEventListener(
                "click",
                function() {
                    spotifyPlayTrack(
                        track,
                        div,
                        playButton
                    );
                }
            );

            stopButton.addEventListener(
                "click",
                function() {
                    stopPreview();
                }
            );

            downloadButton.addEventListener(
                "click",
                async function() {

                    downloadButton.disabled = true;
                    downloadButton.innerText =
                        "⏳ Buscando...";

                    try {

                        const response =
                            await fetch(
                                "/api/spotify/download",
                                {
                                    method: "POST",
                                    headers: {
                                        "Content-Type":
                                            "application/json"
                                    },
                                    body: JSON.stringify({
                                        tracks: [track]
                                    })
                                }
                            );

                        const data =
                            await response.json();

                        if (!response.ok) {
                            throw new Error(
                                data.error ||
                                "No se pudo descargar."
                            );
                        }

                        if (
                            data.errors &&
                            data.errors.length
                        ) {
                            throw new Error(
                                data.errors[0].error ||
                                "No se pudo encontrar la canción."
                            );
                        }

                        downloadButton.innerText =
                            "✓ En cola";

                        const queuedJob =
                            data.queued &&
                            data.queued.length
                                ? data.queued[0].job
                                : null;

                        if (queuedJob) {

                            spotifyWatchDownload(
                                queuedJob,
                                div
                            );
                        }

                    } catch (error) {

                        downloadButton.innerText =
                            "❌ Error";

                        alert(error.message);

                        setTimeout(
                            function() {
                                downloadButton.innerText =
                                    "↓ Descargar";
                                downloadButton.disabled =
                                    false;
                            },
                            2500
                        );

                        return;
                    }

                    setTimeout(
                        function() {
                            downloadButton.innerText =
                                "↓ Descargar";
                            downloadButton.disabled =
                                false;
                        },
                        2500
                    );
                }
            );

            list.appendChild(div);

        }
    );
}


let spotifyPlayback = {
    active: false,
    index: -1,
    tracks: []
};


function spotifyStopPlaylist() {

    spotifyPlayback.active = false;
    spotifyPlayback.index = -1;
    spotifyPlayback.tracks = [];

    stopPreview();

    const status =
        document.getElementById(
            "spotifyStatus"
        );

    if (status) {
        status.innerText =
            "⏹ Reproducción detenida.";
    }
}


async function spotifyPlayPlaylist() {

    if (
        !spotifyPlaylist ||
        !spotifyPlaylist.tracks ||
        !spotifyPlaylist.tracks.length
    ) {

        alert(
            "No hay canciones en la lista."
        );

        return;
    }

    spotifyStopPlaylist();

    spotifyPlayback.tracks =
        spotifyPlaylist.tracks.slice();

    spotifyPlayback.active = true;
    spotifyPlayback.index = 0;

    await spotifyPlayIndex(0);
}


async function spotifyPlayIndex(index) {

    if (!spotifyPlayback.active) {
        return;
    }

    const tracks =
        spotifyPlayback.tracks;

    if (
        !tracks ||
        index < 0 ||
        index >= tracks.length
    ) {

        spotifyPlayback.active = false;
        spotifyPlayback.index = -1;

        stopPreview();

        const status =
            document.getElementById(
                "spotifyStatus"
            );

        if (status) {
            status.innerText =
                "✅ Lista terminada.";
        }

        return;
    }

    spotifyPlayback.index = index;

    const track =
        tracks[index];

    const rows =
        document.querySelectorAll(
            ".spotify-track"
        );

    rows.forEach(
        function(row) {

            row.classList.remove(
                "spotify-playing"
            );

        }
    );

    const row =
        rows[index];

    if (row) {

        row.classList.add(
            "spotify-playing"
        );

    }

    const playButton =
        row
            ? row.querySelector(
                ".spotify-play"
            )
            : null;

    await spotifyPlayTrack(
        track,
        row,
        playButton,
        true
    );
}



function spotifyPlayerPrev() {

    if (!spotifyPlayback.active) return;

    const prev = spotifyPlayback.index - 1;

    if (prev < 0) {
        spotifyPlayback.index = 0;
        return;
    }

    spotifyPlayIndex(prev);
}


function spotifyPlayerNext() {

    if (!spotifyPlayback.active) return;

    const next = spotifyPlayback.index + 1;

    if (next >= spotifyPlayback.tracks.length) {
        spotifyPlayback.active = false;
        spotifyPlayback.index = -1;
        stopPreview();

        const status =
            document.getElementById("spotifyStatus");

        if (status) {
            status.innerText = "✅ Lista terminada.";
        }

        return;
    }

    spotifyPlayIndex(next);
}


function spotifyPlayerToggle() {

    const player =
        spotifyPlayback.player;

    if (!player) return;

    const audio =
        player.querySelector("audio");

    if (!audio) return;

    if (audio.paused) {
        audio.play().catch(() => {});
    } else {
        audio.pause();
    }
}


function spotifyPlayerClose() {

    spotifyPlayback.active = false;
    spotifyPlayback.index = -1;

    const player =
        spotifyPlayback.player;

    if (player) {
        player.remove();
    }

    spotifyPlayback.player = null;
    spotifyPlayback.audio = null;

    stopPreview();

    const status =
        document.getElementById("spotifyStatus");

    if (status) {
        status.innerText = "⏹ Reproductor cerrado.";
    }
}


function enableSpotifyPlayerDrag(player) {

    if (!player) {
        return;
    }

    let dragging = false;
    let offsetX = 0;
    let offsetY = 0;

    player.addEventListener("pointerdown", function(e) {

        if (
            e.target.closest("button") ||
            e.target.closest("input") ||
            e.target.closest("audio")
        ) {
            return;
        }

        const rect =
            player.getBoundingClientRect();

        dragging = true;

        offsetX =
            e.clientX - rect.left;

        offsetY =
            e.clientY - rect.top;

        player.style.left =
            rect.left + "px";

        player.style.top =
            rect.top + "px";

        player.style.right =
            "auto";

        player.style.bottom =
            "auto";

        player.setPointerCapture(
            e.pointerId
        );
    });

    player.addEventListener("pointermove", function(e) {

        if (!dragging) {
            return;
        }

        let x =
            e.clientX - offsetX;

        let y =
            e.clientY - offsetY;

        const maxX =
            window.innerWidth -
            player.offsetWidth;

        const maxY =
            window.innerHeight -
            player.offsetHeight;

        x = Math.max(
            0,
            Math.min(x, maxX)
        );

        y = Math.max(
            0,
            Math.min(y, maxY)
        );

        player.style.left =
            x + "px";

        player.style.top =
            y + "px";
    });

    player.addEventListener("pointerup", function(e) {

        dragging = false;

        try {
            player.releasePointerCapture(
                e.pointerId
            );
        } catch (_) {}
    });

    player.addEventListener("pointercancel", function(e) {

        dragging = false;

        try {
            player.releasePointerCapture(
                e.pointerId
            );
        } catch (_) {}
    });
}


async function spotifyPlayTrack(
    track,
    container,
    button,
    sequential = false
) {

    if (!track) {
        return;
    }

    if (!sequential) {
        spotifyPlayback.active = false;
        spotifyPlayback.index = -1;
        spotifyPlayback.tracks = [];
    }

    stopPreview();

    if (button) {

        button.disabled = true;
        button.innerText =
            "⏳ Buscando";

    }

    const artist =
        track.artist || "";

    const title =
        track.title || "";

    const query =
        artist
            ? artist + " - " + title
            : title;

    const status =
        document.getElementById(
            "spotifyStatus"
        );

    if (status) {

        status.innerText =
            sequential
                ? "🎧 Reproduciendo " +
                  (spotifyPlayback.index + 1) +
                  " de " +
                  spotifyPlayback.tracks.length +
                  ": " +
                  artist +
                  " — " +
                  title
                : "🎧 Buscando: " +
                  artist +
                  " — " +
                  title;

    }

    try {

        const searchResponse =
            await fetch(
                "/api/search?q=" +
                encodeURIComponent(query)
            );

        const searchData =
            await searchResponse.json();

        if (
            !searchData.results ||
            !searchData.results.length
        ) {

            throw new Error(
                "No se encontró la canción en YouTube."
            );

        }

        const result =
            searchData.results[0];

        const id =
            result.id;

        if (!id) {

            throw new Error(
                "YouTube no devolvió un identificador válido."
            );

        }

        if (
            sequential &&
            !spotifyPlayback.active
        ) {
            return;
        }

        const player =
            document.createElement("div");

        /*
         * Reproducción individual:
         * mantiene el reproductor dentro de la canción.
         *
         * Reproducción de lista:
         * usa un reproductor flotante independiente.
         */
        player.className =
            sequential
                ? "player spotify-floating-player"
                : "player";

        player.innerHTML = `

            <button
                class="spotify-floating-close"
                onclick="spotifyPlayerClose()"
                title="Cerrar reproductor">
                ×
            </button>

            <div class="spotify-floating-info">

                <div class="player-info">
                    🎧 Preparando previsualización...
                </div>

                <div class="spotify-floating-position">
                    ${spotifyPlayback.index + 1} de ${spotifyPlayback.tracks.length}
                </div>

            </div>

            <div class="spotify-floating-progress">

                <span class="spotify-time-current">
                    0:00
                </span>

                <input
                    class="spotify-progress"
                    type="range"
                    min="0"
                    max="0"
                    value="0"
                    step="0.1">

                <span class="spotify-time-duration">
                    0:00
                </span>

            </div>

            <div class="spotify-floating-controls">

                <button
                    class="spotify-floating-control"
                    onclick="spotifyPlayerPrev()"
                    title="Anterior">
                    ⏮
                </button>

                <button
                    id="spotify-floating-toggle"
                    class="spotify-floating-control spotify-floating-main"
                    onclick="spotifyPlayerToggle()"
                    title="Play / Pausa">
                    ▶
                </button>

                <button
                    class="spotify-floating-control"
                    onclick="spotifyPlayerNext()"
                    title="Siguiente">
                    ⏭
                </button>

            </div>

            <audio
                autoplay
                preload="auto">
            </audio>

        `;

        if (sequential) {

            /*
             * La lista completa usa un reproductor
             * flotante independiente de las filas.
             */
            document.body.appendChild(player);

            enableSpotifyPlayerDrag(player);

        } else if (container) {

            /*
             * La reproducción individual permanece
             * exactamente donde estaba.
             */
            container.appendChild(player);

        } else {

            const list =
                document.getElementById(
                    "spotifyList"
                );

            if (list) {
                list.appendChild(player);
            }

        }

        currentPlayer = id;

        const response =
            await fetch(
                "/api/preview?id=" +
                encodeURIComponent(id)
            );

        const data =
            await response.json();

        if (data.error) {

            throw new Error(
                data.error
            );

        }

        if (
            sequential &&
            !spotifyPlayback.active
        ) {

            player.remove();
            return;

        }

        const audio =
            player.querySelector("audio");

        const progress =
            player.querySelector(".spotify-progress");

        const currentTimeLabel =
            player.querySelector(".spotify-time-current");

        const durationLabel =
            player.querySelector(".spotify-time-duration");

        function formatSpotifyTime(seconds) {

            if (!Number.isFinite(seconds) || seconds < 0) {
                return "0:00";
            }

            const minutes =
                Math.floor(seconds / 60);

            const secs =
                Math.floor(seconds % 60)
                    .toString()
                    .padStart(2, "0");

            return minutes + ":" + secs;
        }

        function updateSpotifyProgress() {

            if (!audio) {
                return;
            }

            const duration =
                Number.isFinite(audio.duration)
                    ? audio.duration
                    : 0;

            if (progress) {
                progress.max = duration;
                progress.value =
                    Math.min(audio.currentTime || 0, duration);
            }

            if (currentTimeLabel) {
                currentTimeLabel.innerText =
                    formatSpotifyTime(audio.currentTime || 0);
            }

            if (durationLabel) {
                durationLabel.innerText =
                    formatSpotifyTime(duration);
            }
        }

        audio.addEventListener(
            "loadedmetadata",
            function() {

                const duration =
                    Number.isFinite(audio.duration)
                        ? Math.min(audio.duration, 30)
                        : 30;

                if (progress) {
                    progress.max = duration;
                }

                if (durationLabel) {
                    durationLabel.innerText =
                        formatSpotifyTime(duration);
                }

                updateSpotifyProgress();
            }
        );

        audio.addEventListener(
            "timeupdate",
            updateSpotifyProgress
        );

        if (progress) {

            progress.addEventListener(
                "input",
                function() {

                    const value =
                        Number(progress.value);

                    if (Number.isFinite(value)) {
                        audio.currentTime = value;
                    }

                    updateSpotifyProgress();
                }
            );
        }

        audio.addEventListener(
            "play",
            function() {

                const toggle =
                    player.querySelector(
                        "#spotify-floating-toggle"
                    );

                if (toggle) {
                    toggle.innerText = "⏸";
                    toggle.title = "Pausa";
                }
            }
        );

        audio.addEventListener(
            "pause",
            function() {

                const toggle =
                    player.querySelector(
                        "#spotify-floating-toggle"
                    );

                if (toggle) {
                    toggle.innerText = "▶";
                    toggle.title = "Play";
                }
            }
        );

        if (sequential) {

            spotifyPlayback.player =
                player;

            spotifyPlayback.audio =
                audio;
        }

        audio.src =
            data.url;

        const info =
            player.querySelector(
                ".player-info"
            );

        info.innerText =
            "🎧 " +
            (artist
                ? artist + " — " + title
                : title);

        audio.onended =
            async function() {

                if (!spotifyPlayback.active) {
                    return;
                }

                const nextIndex =
                    spotifyPlayback.index + 1;

                if (
                    nextIndex >=
                    spotifyPlayback.tracks.length
                ) {

                    spotifyPlayback.active = false;
                    spotifyPlayback.index = -1;

                    stopPreview();

                    if (status) {
                        status.innerText =
                            "✅ Lista terminada.";
                    }

                    return;
                }

                await spotifyPlayIndex(
                    nextIndex
                );

            };

        audio.play().catch(() => {});

        if (button) {

            button.disabled = false;
            button.classList.add("active");

            button.innerText =
                sequential
                    ? "⏹ Reproduciendo"
                    : "⏹ Reproduciendo";

        }

    } catch (error) {

        if (sequential) {

            /*
             * Si una canción falla durante la
             * reproducción automática,
             * continuamos con la siguiente.
             */

            const nextIndex =
                spotifyPlayback.index + 1;

            if (
                spotifyPlayback.active &&
                nextIndex <
                spotifyPlayback.tracks.length
            ) {

                await spotifyPlayIndex(
                    nextIndex
                );

                return;
            }

            spotifyPlayback.active = false;
            spotifyPlayback.index = -1;

        }

        const oldPlayer =
            container
                ? container.querySelector(
                    ".player"
                )
                : null;

        if (oldPlayer) {
            oldPlayer.remove();
        }

        if (button) {

            button.disabled = false;
            button.innerText =
                "▶ Escuchar";

            button.classList.remove(
                "active"
            );

        }

        currentPlayer = null;

        if (status) {

            status.innerText =
                "❌ " +
                (
                    error.message ||
                    "No se pudo preparar la previsualización."
                );

        }

        if (!sequential) {

            alert(
                error.message ||
                "No se pudo preparar la previsualización."
            );

        }

    }
}

function spotifySelectAll() {

    const boxes =
        document.querySelectorAll(
            ".spotify-check"
        );

    if (!boxes.length) {
        return;
    }

    let allChecked = true;

    boxes.forEach(
        function(box) {

            if (!box.checked) {
                allChecked = false;
            }

        }
    );

    boxes.forEach(
        function(box) {
            box.checked = !allChecked;
        }
    );
}


async function spotifyWatchDownload(
    job,
    card
) {

    if (!job || !card) {
        return;
    }

    const statusBox =
        card.querySelector(
            ".spotify-download-status"
        );

    if (!statusBox) {
        return;
    }

    let finished = false;

    while (!finished) {

        try {

            const response =
                await fetch(
                    "/api/queue",
                    {
                        cache: "no-store"
                    }
                );

            const data =
                await response.json();

            const queue =
                data.queue || [];

            const history =
                data.history || [];

            const active =
                queue.find(
                    function(item) {
                        return item.job === job;
                    }
                );

            if (active) {

                const progress =
                    Number(
                        active.progress || 0
                    );

                let message =
                    active.message ||
                    "Descargando...";

                if (
                    active.status === "queued"
                ) {

                    statusBox.className =
                        "spotify-download-status spotify-download-status-loading";

                    statusBox.innerHTML =
                        "⏳ " +
                        spotifyEscape(
                            message
                        );

                } else {

                    statusBox.className =
                        "spotify-download-status spotify-download-status-progress";

                    statusBox.innerHTML =
                        "<div>📥 " +
                        spotifyEscape(
                            message
                        ) +
                        " · " +
                        progress +
                        "%</div>" +

                        "<div class=\"spotify-download-progress\">" +
                            "<div style=\"width:" +
                            Math.max(
                                0,
                                Math.min(
                                    100,
                                    progress
                                )
                            ) +
                            "%\"></div>" +
                        "</div>";
                }

            } else {

                const completed =
                    history.find(
                        function(item) {
                            return item.job === job;
                        }
                    );

                if (completed) {

                    const completedStatus =
                        String(
                            completed.status || ""
                        ).toLowerCase();

                    if (
                        completedStatus === "error" ||
                        completedStatus === "failed" ||
                        completedStatus === "cancelled"
                    ) {

                        statusBox.className =
                            "spotify-download-status spotify-download-status-error";

                        statusBox.innerText =
                            "❌ " +
                            (
                                completed.message ||
                                "Error en la descarga."
                            );

                    } else {

                        statusBox.className =
                            "spotify-download-status spotify-download-status-success";

                        statusBox.innerText =
                            "✅ Enviado a Navidrome";
                    }

                    finished = true;

                } else {

                    /*
                     * Puede haber un pequeño intervalo entre
                     * desaparecer de la cola y aparecer en historial.
                     */
                    statusBox.className =
                        "spotify-download-status spotify-download-status-progress";

                    statusBox.innerText =
                        "📦 Finalizando...";
                }
            }

        } catch (error) {

            console.error(
                "Error consultando progreso Spotify:",
                error
            );
        }

        if (!finished) {

            await new Promise(
                function(resolve) {
                    setTimeout(
                        resolve,
                        1000
                    );
                }
            );
        }
    }
}


async function spotifyDownloadSelected() {

    if (
        !spotifyPlaylist ||
        !spotifyPlaylist.tracks
    ) {
        return;
    }

    const boxes =
        Array.from(
            document.querySelectorAll(
                ".spotify-check:checked"
            )
        );

    if (!boxes.length) {

        alert(
            "Selecciona al menos una canción."
        );

        return;
    }

    const status =
        document.getElementById(
            "spotifyStatus"
        );

    status.innerText =
        "⏳ Añadiendo canciones seleccionadas...";

    let added = 0;
    let errors = 0;

    /*
     * Procesamos cada tarjeta por separado.
     *
     * Esto utiliza exactamente el mismo mecanismo
     * que la descarga individual que ya funciona:
     *
     * tarjeta -> /api/spotify/download -> job
     * -> spotifyWatchDownload(job, tarjeta)
     *
     * De esta forma cada job queda ligado directamente
     * a SU tarjeta y nunca dependemos del orden de
     * data.queued.
     */

    for (
        let i = 0;
        i < boxes.length;
        i++
    ) {

        const box =
            boxes[i];

        const trackIndex =
            parseInt(
                box.dataset.index
            );

        const track =
            spotifyPlaylist.tracks[
                trackIndex
            ];

        const card =
            box.closest(
                ".spotify-track"
            );

        if (!track || !card) {
            errors++;
            continue;
        }

        const statusBox =
            card.querySelector(
                ".spotify-download-status"
            );

        /*
         * El estado aparece inmediatamente
         * dentro de ESTA tarjeta.
         */

        if (statusBox) {

            statusBox.className =
                "spotify-download-status spotify-download-status-loading";

            statusBox.innerText =
                "⏳ Buscando...";
        }

        try {

            const response =
                await fetch(
                    "/api/spotify/download",
                    {
                        method: "POST",
                        headers: {
                            "Content-Type":
                                "application/json"
                        },
                        body: JSON.stringify({
                            tracks: [track]
                        })
                    }
                );

            const data =
                await response.json();

            if (!response.ok) {

                throw new Error(
                    data.error ||
                    "No se pudo descargar."
                );
            }

            if (
                data.errors &&
                data.errors.length
            ) {

                throw new Error(
                    data.errors[0].error ||
                    "No se pudo encontrar la canción."
                );
            }

            const queuedJob =
                data.queued &&
                data.queued.length
                    ? data.queued[0].job
                    : null;

            if (!queuedJob) {

                throw new Error(
                    "No se recibió el trabajo de descarga."
                );
            }

            added++;

            /*
             * AQUÍ está la parte importante:
             * el job se entrega directamente a la
             * tarjeta que corresponde a este checkbox.
             */

            spotifyWatchDownload(
                queuedJob,
                card
            );

        } catch (error) {

            errors++;

            if (statusBox) {

                statusBox.className =
                    "spotify-download-status spotify-download-status-error";

                statusBox.innerText =
                    "❌ " +
                    error.message;
            }

        }

    }

    let message =
        "✅ " +
        added +
        " canciones añadidas a la cola.";

    if (errors) {

        message +=
            " " +
            errors +
            " no se pudieron añadir.";
    }

    status.innerText =
        message;

    if (
        typeof updateQueue === "function"
    ) {
        updateQueue();
    }
}



    try {
        loadSpotifySavedLists();
    } catch (error) {
        console.error(
            "Error inicializando historial Spotify:",
            error
        );
    }

</script>


<!-- FIN ALBUM_SEARCH_SECTION_V2 -->

</body>

</html>
"""


def search_youtube(query):

    command = [
        "yt-dlp",
        "--flat-playlist",
        "--dump-single-json",
        "--skip-download",
        f"ytsearch100:{query}"
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=90
    )

    # yt-dlp puede devolver resultados válidos aunque
    # alguna página posterior falle. En ese caso intentamos
    # aprovechar el JSON recibido antes de devolver un error.
    if not result.stdout.strip():

        raise RuntimeError(
            result.stderr.strip()
            or "yt-dlp ha fallado"
        )

    try:
        data = json.loads(result.stdout)

    except json.JSONDecodeError:

        raise RuntimeError(
            result.stderr.strip()
            or "yt-dlp no devolvió un JSON válido"
        )

    entries = data.get("entries", [])

    output = []

    for item in entries:

        if not isinstance(item, dict):
            continue

        video_id = item.get("id")

        if not video_id:
            continue

        thumbnail = (
            item.get("thumbnail")
            or
            f"https://i.ytimg.com/vi/"
            f"{video_id}/hqdefault.jpg"
        )

        output.append({

            "id": video_id,

            "title":
                item.get(
                    "title",
                    "Sin título"
                ),

            "channel":
                item.get("channel")
                or
                item.get("uploader")
                or "",

            "thumbnail":
                thumbnail,

            "duration":
                item.get("duration")

        })

    # Solo consideramos error si no hemos obtenido
    # absolutamente ningún resultado.
    if not output and result.returncode != 0:

        raise RuntimeError(
            result.stderr.strip()
            or "yt-dlp no encontró resultados"
        )

    return output


def get_preview_url(video_id):

    url = (
        f"https://www.youtube.com/watch?v={video_id}"
    )

    command = [
        "yt-dlp",
        "--force-ipv4",
        "--remote-components",
        "ejs:github",
        "--js-runtimes",
        "deno:/home/franrpi/.deno/bin/deno",
        "--no-playlist",
        "-f",
        "bestaudio[ext=m4a]/bestaudio",
        "-g",
        url
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=60
    )

    if result.returncode != 0:

        raise RuntimeError(
            result.stderr.strip()
            or
            "No se pudo obtener el audio."
        )

    preview_url = result.stdout.strip().splitlines()

    if not preview_url:

        raise RuntimeError(
            "YouTube no devolvió una URL de audio."
        )

    return preview_url[0]


def do_download(
        job,
        video_id,
        title="",
        album_group=None,
        album_title=None,
        album_track_index=0,
        album_track_total=0):

    # Navidrome usa siempre MP3.
    format_name = "mp3"

    with download_lock:

        process = None


        try:

            downloads[job] = {
                "status": "running",
                "message": "Descargando...",
                "progress": 0,
                "title": title or video_id,
                "id": video_id,
                "type": "navidrome",
                "album_group": album_group,
                "album_title": album_title,
                "album_track_index": album_track_index,
                "album_track_total": album_track_total,
                "format": format_name
            }

            url = (
                f"https://www.youtube.com/watch?v={video_id}"
            )

            command = [

                "yt-dlp",

                "--js-runtimes",
                "node",

                "--newline",

                "--progress-template",
                "%(progress._percent_str)s",

                "--no-overwrites",

                "--continue",

                "-x",

                "--audio-format",
                "mp3",

                "--audio-quality",
                "0",

                "--embed-thumbnail",

                "--add-metadata",

                "--parse-metadata",
                "%(channel)s:%(artist)s",

                "-o",

                str(
                    DOWNLOAD_DIR /
                    "%(artist)s" /
                    "%(album)s" /
                    "%(title)s.%(ext)s"
                ),

                url
            ]

            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            active_processes[job] = process

            output_lines = []

            start_time = time.time()
            last_state_save = 0

            while True:

                line = process.stdout.readline()

                if line:
                    output_lines.append(line)

                    if len(output_lines) > 100:
                        output_lines.pop(0)

                    match = re.search(
                        r"(\d+(?:\.\d+)?)%",
                        line
                    )

                    if match:

                        try:
                            percent = float(
                                match.group(1)
                            )

                            downloads[job]["progress"] = min(
                                100,
                                max(0, round(percent))
                            )

                            downloads[job]["message"] = (
                                "Descargando... "
                                + str(
                                    downloads[job]["progress"]
                                )
                                + "%"
                            )

                        except Exception:
                            pass

                elif process.poll() is not None:
                    break

                pause_started = None

                with download_control_lock:
                    pause_started = download_pause_started.get(job)

                paused_elapsed = 0

                if pause_started is not None:
                    paused_elapsed = time.time() - pause_started

                active_time = (
                    time.time()
                    - start_time
                    - paused_elapsed
                )

                if active_time > 600:

                    process.kill()

                    raise RuntimeError(
                        "Tiempo de descarga agotado."
                    )

            returncode = process.wait()

            if returncode != 0:

                raise RuntimeError(
                    "".join(output_lines)[-2000:]
                    or
                    "Error durante la descarga"
                )

            downloads[job]["progress"] = 100

            downloads[job]["message"] = (
                "Descarga terminada. Procesando..."
            )

            downloads[job]["message"] = (
                "Procesando etiquetas y portada..."
            )

            mp3_files = sorted(
                DOWNLOAD_DIR.rglob("*.mp3"),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )

            if not mp3_files:

                raise RuntimeError(
                    "No se encontró el MP3 descargado."
                )

            mp3 = mp3_files[0]

            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-show_entries",
                    "format_tags=artist",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(mp3)
                ],
                capture_output=True,
                text=True
            )

            artist = probe.stdout.strip()

            if not artist:
                artist = "Desconocido"

            tag_result = subprocess.run(
                [
                    "python3",
                    str(TAG_SCRIPT),
                    str(mp3),
                    artist
                ],
                capture_output=True,
                text=True,
                timeout=180
            )

            if tag_result.returncode != 0:

                raise RuntimeError(
                    tag_result.stderr[-2000:]
                    or
                    "Error procesando metadatos"
                )

            downloads[job] = {
                "status": "done",
                "message":
                    "Canción subida a la carpeta de Navidrome",
                "progress": 100,
                "title": title or video_id,
                "id": video_id,
                "type": "navidrome",
                "album_group": album_group,
                "album_title": album_title,
                "album_track_index": album_track_index,
                "album_track_total": album_track_total
            }

            add_history(
                job,
                video_id,
                title
            )

            active_processes.pop(
                job,
                None
            )

            clear_download_control(job)

        except Exception as error:

            active_processes.pop(
                job,
                None
            )

            control = get_download_control(job)

            if control == "cancelled":

                downloads[job] = {
                    "status": "cancelled",
                    "message": "Descarga cancelada.",
                    "progress": downloads.get(
                        job,
                        {}
                    ).get(
                        "progress",
                        0
                    ),
                    "title": title or video_id,
                    "id": video_id,
                    "type": "navidrome",
                    "album_group": album_group,
                    "album_title": album_title,
                    "album_track_index": album_track_index,
                    "album_track_total": album_track_total
                }

            else:

                downloads[job] = {
                    "status": "error",
                    "message": str(error),
                    "progress": 0,
                    "title": title or video_id,
                    "id": video_id,
                    "type": "navidrome",
                    "album_group": album_group,
                    "album_title": album_title,
                    "album_track_index": album_track_index,
                    "album_track_total": album_track_total
                }

            add_history(
                job,
                video_id,
                title
            )

            clear_download_control(job)




# ALBUM_BACKEND_FINAL

def album_musicbrainz_search(query):

    url = (
        "https://musicbrainz.org/ws/2/release/"
        "?query=" + urllib.parse.quote(query)
        + "&fmt=json&limit=10"
    )

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent":
                "MusicDownloader/1.0 (RaspberryPi)"
        }
    )

    with urllib.request.urlopen(
        request,
        timeout=30
    ) as response:

        data = json.loads(
            response.read().decode("utf-8")
        )

    releases = data.get("releases", [])

    if not releases:
        return None

    for release in releases:

        title = release.get(
            "title",
            ""
        ).strip()

        artist = ""

        for credit in release.get(
            "artist-credit",
            []
        ):

            if isinstance(credit, dict):

                artist_data = credit.get(
                    "artist",
                    {}
                )

                if isinstance(
                    artist_data,
                    dict
                ):
                    artist += artist_data.get(
                        "name",
                        ""
                    )

                artist += credit.get(
                    "joinphrase",
                    ""
                )

        if title:

            return {
                "id": release.get("id"),
                "title": title,
                "artist": artist.strip()
            }

    return None


def album_musicbrainz_tracks(release_id):

    url = (
        "https://musicbrainz.org/ws/2/release/"
        + urllib.parse.quote(release_id)
        + "?inc=recordings+artist-credits"
        + "&fmt=json"
    )

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent":
                "MusicDownloader/1.0 (RaspberryPi)"
        }
    )

    with urllib.request.urlopen(
        request,
        timeout=30
    ) as response:

        data = json.loads(
            response.read().decode("utf-8")
        )

    tracks = []

    for medium in data.get(
        "media",
        []
    ):

        for track in medium.get(
            "tracks",
            []
        ):

            recording = track.get(
                "recording",
                {}
            )

            title = (
                recording.get("title")
                or track.get("title")
                or ""
            ).strip()

            if not title:
                continue

            tracks.append({
                "position":
                    len(tracks) + 1,
                "title":
                    title
            })

    return tracks


def album_find_youtube(artist, title):

    query = (
        artist + " " + title
    ).strip()

    try:

        command = [
            "yt-dlp",
            "--flat-playlist",
            "--dump-single-json",
            "--skip-download",
            "ytsearch10:" + query
        ]

        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=20
        )

        if process.returncode != 0:
            return None

        data = json.loads(
            process.stdout
        )

        entries = data.get(
            "entries",
            []
        )

        if not entries:
            return None

        wanted = title.lower()

        # Primera opción: título de YouTube
        # que contenga el nombre exacto.
        for item in entries:

            item_title = str(
                item.get(
                    "title",
                    ""
                )
            )

            if wanted in item_title.lower():

                return item

        return entries[0]

    except Exception as error:

        print(
            "ERROR buscando canción:",
            title,
            repr(error)
        )

        return None


def search_album_backend(query):
    """
    Busca álbumes en MusicBrainz.

    - Si la búsqueda corresponde a un artista, devuelve varios álbumes.
    - Si corresponde a un álbum concreto, devuelve directamente sus canciones.
    """

    query = (query or "").strip()

    if not query:
        return {
            "title": "",
            "artist": "",
            "tracks": []
        }

    headers = {
        "User-Agent": "MusicDownloader/1.0 (RaspberryPi)"
    }

    def musicbrainz_request(url):

        ultimo_error = None

        for intento in range(1, 4):

            try:

                print(
                    f"MusicBrainz intento {intento}/3:",
                    url,
                    flush=True
                )

                request = urllib.request.Request(
                    url,
                    headers=headers
                )

                with urllib.request.urlopen(
                    request,
                    timeout=15
                ) as response:

                    return json.loads(
                        response.read().decode("utf-8")
                    )

            except Exception as error:

                ultimo_error = error

                print(
                    f"ERROR MusicBrainz intento {intento}/3:",
                    repr(error),
                    flush=True
                )

                if intento < 3:
                    time.sleep(intento)

        raise ultimo_error

    # -----------------------------------------------------
    # 1. Comprobar si la búsqueda corresponde a un artista
    # -----------------------------------------------------

    artist_url = (
        "https://musicbrainz.org/ws/2/artist/"
        "?query=" + urllib.parse.quote(query)
        + "&fmt=json&limit=5"
    )

    try:
        artist_data = musicbrainz_request(artist_url)
    except Exception as error:

        print(
            "ERROR MusicBrainz artista:",
            repr(error),
            flush=True
        )

        artist_data = {}

    artists = artist_data.get("artists", [])

    matched_artist = None

    for item in artists:

        name = str(
            item.get("name", "")
        ).strip()

        if name.lower() == query.lower():

            matched_artist = item
            break

    # -----------------------------------------------------
    # 2. Si es un artista, buscar sus release-groups
    # -----------------------------------------------------

    if matched_artist:

        artist_id = matched_artist.get("id")
        artist_name = matched_artist.get("name", query)

        if artist_id:

            release_group_url = (
                "https://musicbrainz.org/ws/2/release-group/"
                "?artist=" + urllib.parse.quote(artist_id)
                + "&fmt=json&limit=100"
            )

            try:

                group_data = musicbrainz_request(
                    release_group_url
                )

                groups = group_data.get(
                    "release-groups",
                    []
                )

            except Exception as error:

                print(
                    "ERROR MusicBrainz release-groups:",
                    repr(error),
                    flush=True
                )

                groups = []

            albums = []

            for group in groups:

                title = str(
                    group.get("title", "")
                ).strip()

                if not title:
                    continue

                group_type = str(
                    group.get("primary-type", "")
                ).strip()

                if group_type not in (
                    "",
                    "Album",
                    "EP"
                ):
                    continue

                albums.append({
                    "id": group.get("id", ""),
                    "title": title,
                    "artist": artist_name,
                    "date": group.get("first-release-date", ""),
                    "type": group_type,
                    "tracks": []
                })

            albums.sort(
                key=lambda x: (
                    x.get("date") or "9999",
                    x.get("title", "").lower()
                )
            )

            if albums:

                return {
                    "albums": albums,
                    "artist": artist_name
                }

    # -----------------------------------------------------
    # 3. No es artista: buscar álbum concreto
    # -----------------------------------------------------

    search_url = (
        "https://musicbrainz.org/ws/2/release/"
        "?query=" + urllib.parse.quote(query)
        + "&fmt=json&limit=1"
    )

    try:

        data = musicbrainz_request(
            search_url
        )

    except Exception as error:

        print(
            "ERROR MusicBrainz búsqueda:",
            repr(error),
            flush=True
        )

        raise RuntimeError(
            "MusicBrainz está temporalmente ocupado. Inténtalo de nuevo en unos segundos."
        )

    releases = data.get(
        "releases",
        []
    )

    if not releases:

        return {
            "title": "",
            "artist": "",
            "tracks": []
        }

    release = releases[0]

    release_id = release.get("id")

    if not release_id:

        raise RuntimeError(
            "MusicBrainz no devolvió el identificador del álbum."
        )

    album_title = release.get(
        "title",
        query
    )

    artist = ""

    artist_credit = release.get(
        "artist-credit",
        []
    )

    if artist_credit:

        artist = (
            artist_credit[0]
            .get("artist", {})
            .get("name", "")
        )

    # -----------------------------------------------------
    # 4. Obtener detalle y canciones
    # -----------------------------------------------------

    detail_url = (
        "https://musicbrainz.org/ws/2/release/"
        + urllib.parse.quote(release_id)
        + "?inc=recordings%2Bartist-credits&fmt=json"
    )

    try:

        detail = musicbrainz_request(
            detail_url
        )

    except Exception as error:

        print(
            "ERROR MusicBrainz detalle:",
            repr(error),
            flush=True
        )

        raise RuntimeError(
            "No se pudo obtener el contenido del álbum."
        )

    tracks = []
    track_number = 0

    for medium in detail.get(
        "media",
        []
    ):

        for track in medium.get(
            "tracks",
            []
        ):

            recording = track.get(
                "recording",
                {}
            )

            title = (
                recording.get("title")
                or track.get("title")
                or ""
            ).strip()

            if not title:
                continue

            track_number += 1

            duration_ms = (
                recording.get("length")
                or track.get("length")
                or 0
            )

            duration = 0

            try:

                if duration_ms:
                    duration = int(
                        duration_ms
                    ) // 1000

            except Exception:
                duration = 0

            tracks.append({
                "number": track_number,
                "title": title,
                "artist": artist,
                "album": album_title,
                "id": "",
                "video_id": "",
                "thumbnail": "",
                "duration": duration
            })

    return {
        "id": release_id,
        "title": album_title,
        "artist": artist,
        "tracks": tracks
    }



def load_album_by_release_group(
    release_group_id,
    album_title="",
    artist=""
):
    """
    Obtiene una edición del release-group y devuelve
    el álbum con sus canciones.
    """

    headers = {
        "User-Agent": "MusicDownloader/1.0 (RaspberryPi)"
    }

    def request(url):

        ultimo_error = None

        for intento in range(1, 4):

            try:

                print(
                    f"MusicBrainz álbum intento {intento}/3:",
                    url,
                    flush=True
                )

                req = urllib.request.Request(
                    url,
                    headers=headers
                )

                with urllib.request.urlopen(
                    req,
                    timeout=15
                ) as response:

                    return json.loads(
                        response.read().decode("utf-8")
                    )

            except Exception as error:

                ultimo_error = error

                print(
                    "ERROR MusicBrainz álbum:",
                    repr(error),
                    flush=True
                )

                if intento < 3:
                    time.sleep(intento)

        raise ultimo_error

    if not release_group_id:
        raise RuntimeError(
            "No se recibió el identificador del álbum."
        )

    # Buscar las ediciones pertenecientes al release-group
    url = (
        "https://musicbrainz.org/ws/2/release/"
        "?release-group=" +
        urllib.parse.quote(release_group_id) +
        "&fmt=json&limit=1"
    )

    data = request(url)

    releases = data.get(
        "releases",
        []
    )

    if not releases:

        raise RuntimeError(
            "No se encontró una edición del álbum."
        )

    release = releases[0]

    release_id = release.get("id")

    if not release_id:

        raise RuntimeError(
            "MusicBrainz no devolvió el ID de la edición."
        )

    # Obtener detalle con canciones
    detail_url = (
        "https://musicbrainz.org/ws/2/release/" +
        urllib.parse.quote(release_id) +
        "?inc=recordings%2Bartist-credits&fmt=json"
    )

    detail = request(detail_url)

    album_title = (
        detail.get("title")
        or album_title
        or "Álbum"
    )

    if not artist:

        artist_credit = detail.get(
            "artist-credit",
            []
        )

        if artist_credit:

            artist = (
                artist_credit[0]
                .get("artist", {})
                .get("name", "")
            )

    tracks = []
    track_number = 0

    for medium in detail.get(
        "media",
        []
    ):

        for track in medium.get(
            "tracks",
            []
        ):

            recording = track.get(
                "recording",
                {}
            )

            title = (
                recording.get("title")
                or track.get("title")
                or ""
            ).strip()

            if not title:
                continue

            track_number += 1

            duration_ms = (
                recording.get("length")
                or track.get("length")
                or 0
            )

            duration = 0

            try:

                if duration_ms:
                    duration = (
                        int(duration_ms) // 1000
                    )

            except Exception:
                duration = 0

            tracks.append({
                "number": track_number,
                "title": title,
                "artist": artist,
                "album": album_title,
                "id": "",
                "video_id": "",
                "thumbnail": "",
                "duration": duration
            })

    return {
        "id": release_id,
        "release_group_id": release_group_id,
        "title": album_title,
        "artist": artist,
        "tracks": tracks
    }



# ============================================================
# SPOTIFY PLAYLIST IMPORT
# CSV / TXT / JSON
# ============================================================

def _spotify_clean(value):
    if value is None:
        return ""
    return str(value).strip()


def _spotify_normalize(value):
    value = _spotify_clean(value).lower()
    value = value.replace("_", " ")
    value = value.replace("-", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _spotify_pick(row, names):
    normalized = {
        _spotify_normalize(k): v
        for k, v in row.items()
        if k is not None
    }

    for name in names:
        value = normalized.get(
            _spotify_normalize(name),
            ""
        )

        if _spotify_clean(value):
            return _spotify_clean(value)

    return ""


def spotify_parse_csv(content):
    content = content.lstrip("\ufeff")

    try:
        sample = content[:5000]
        dialect = csv.Sniffer().sniff(
            sample,
            delimiters=",;\t|"
        )
    except Exception:
        dialect = csv.excel

    reader = csv.DictReader(
        io.StringIO(content),
        dialect=dialect
    )

    tracks = []

    for position, row in enumerate(reader, 1):

        title = _spotify_pick(
            row,
            [
                "Track Name",
                "Track",
                "Song Name",
                "Song",
                "Name",
                "Title"
            ]
        )

        artist = _spotify_pick(
            row,
            [
                "Artist Name(s)",
                "Artist Names",
                "Artists",
                "Artist",
                "Track Artist",
                "Artist Name"
            ]
        )

        album = _spotify_pick(
            row,
            [
                "Album Name",
                "Album",
                "Record"
            ]
        )

        uri = _spotify_pick(
            row,
            [
                "Track URI",
                "Spotify URI",
                "URI"
            ]
        )

        duration = _spotify_pick(
            row,
            [
                "Duration_ms",
                "Duration (ms)",
                "Duration ms",
                "Duration",
                "Track Duration",
                "Track Duration (ms)"
            ]
        )

        if not title:
            continue

        tracks.append({
            "position": position,
            "title": title,
            "artist": artist,
            "album": album,
            "uri": uri,
            "duration": duration
        })

    return tracks


def spotify_parse_txt(content):
    content = content.lstrip("\ufeff")

    tracks = []

    for position, raw_line in enumerate(
        content.splitlines(),
        1
    ):

        line = raw_line.strip()

        if not line:
            continue

        if line.startswith("#"):
            continue

        # Formato:
        # Artista - Canción
        # Canción - Artista
        # Artista -- Canción
        # Canción -- Artista
        parts = re.split(
            r"\s+--\s+|\s+-\s+|\s+\|\s+",
            line,
            maxsplit=1
        )

        if len(parts) == 2:

            first = parts[0].strip()
            second = parts[1].strip()

            artist = first
            title = second

        else:

            title = line
            artist = ""

        # Eliminar numeración inicial
        title = re.sub(
            r"^\s*\d+\s*[\.\)\-:]\s*",
            "",
            title
        ).strip()

        if not title:
            continue

        tracks.append({
            "position": position,
            "title": title,
            "artist": artist,
            "album": "",
            "uri": "",
            "duration": ""
        })

    return tracks


def spotify_json_find_tracks(data):

    found = []

    def walk(obj):

        if isinstance(obj, list):

            for item in obj:
                walk(item)

            return

        if not isinstance(obj, dict):
            return

        # Formato Spotify / Exportify:
        # track.name
        # track.artists
        if isinstance(obj.get("track"), dict):

            track = obj["track"]

            if (
                track.get("name")
                or
                track.get("title")
            ):

                found.append(track)

        # También aceptamos directamente:
        # {name, artist, album}
        if (
            obj.get("name")
            and
            (
                obj.get("artist")
                or
                obj.get("artists")
                or
                obj.get("album")
            )
        ):

            found.append(obj)

        for key, value in obj.items():

            if key in (
                "items",
                "tracks",
                "playlist",
                "songs",
                "data"
            ):

                walk(value)

    walk(data)

    return found


def spotify_parse_json(content):

    content = content.lstrip("\ufeff")

    data = json.loads(content)

    raw_tracks = spotify_json_find_tracks(data)

    tracks = []

    for position, item in enumerate(
        raw_tracks,
        1
    ):

        title = _spotify_clean(
            item.get("name")
            or
            item.get("title")
            or
            item.get("track_name")
        )

        artists = item.get("artists", "")

        if isinstance(artists, list):

            names = []

            for artist in artists:

                if isinstance(artist, dict):
                    name = artist.get("name", "")
                else:
                    name = str(artist)

                if name:
                    names.append(str(name))

            artist = ", ".join(names)

        else:

            artist = _spotify_clean(
                item.get("artist")
                or
                item.get("artist_name")
                or
                item.get("artists")
            )

        album_value = item.get("album", "")

        if isinstance(album_value, dict):
            album = _spotify_clean(
                album_value.get("name", "")
            )
        else:
            album = _spotify_clean(
                album_value
            )

        uri = _spotify_clean(
            item.get("uri")
            or
            item.get("track_uri")
            or
            item.get("spotify_uri")
        )

        duration = _spotify_clean(
            item.get("duration_ms")
            or
            item.get("duration")
        )

        if not title:
            continue

        tracks.append({
            "position": position,
            "title": title,
            "artist": artist,
            "album": album,
            "uri": uri,
            "duration": duration
        })

    return tracks



# ============================================================
# Spotify - historial persistente de playlists
# ============================================================

SPOTIFY_LISTS_FILE = "/opt/music-downloader/data/spotify_playlists.json"

def load_spotify_lists():
    try:
        if not os.path.exists(SPOTIFY_LISTS_FILE):
            return []

        with open(
            SPOTIFY_LISTS_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            data = json.load(f)

        if not isinstance(data, list):
            return []

        return data

    except Exception as error:
        print(
            "ERROR cargando historial Spotify:",
            repr(error)
        )
        return []


def save_spotify_lists(lists):
    try:
        os.makedirs(
            os.path.dirname(SPOTIFY_LISTS_FILE),
            exist_ok=True
        )

        tmp = SPOTIFY_LISTS_FILE + ".tmp"

        with open(
            tmp,
            "w",
            encoding="utf-8"
        ) as f:
            json.dump(
                lists,
                f,
                ensure_ascii=False,
                indent=2
            )

        os.replace(
            tmp,
            SPOTIFY_LISTS_FILE
        )

        return True

    except Exception as error:
        print(
            "ERROR guardando historial Spotify:",
            repr(error)
        )
        return False


def save_spotify_playlist(result):
    if not isinstance(result, dict):
        return

    tracks = result.get("tracks", [])

    if not isinstance(tracks, list):
        tracks = []

    name = str(
        result.get("name") or "Playlist"
    ).strip()

    from datetime import datetime

    playlist = {
        "id": os.urandom(8).hex(),
        "name": name,
        "created_at": datetime.now().isoformat(
            timespec="seconds"
        ),
        "count": len(tracks),
        "tracks": tracks
    }

    lists = load_spotify_lists()

    # Si ya existe una playlist con el mismo nombre,
    # sustituimos su contenido por la versión nueva.
    replaced = False

    for index, old in enumerate(lists):
        if str(
            old.get("name", "")
        ).strip().lower() == name.lower():

            playlist["id"] = old.get(
                "id",
                playlist["id"]
            )

            lists[index] = playlist
            replaced = True
            break

    if not replaced:
        lists.insert(0, playlist)

    # Las más recientes primero.
    lists = lists[:100]

    save_spotify_lists(lists)

    return playlist


def spotify_parse_spotify_url(url):

    url = (url or "").strip()

    if not url:
        raise ValueError(
            "La URL de Spotify está vacía."
        )

    parsed = urllib.parse.urlparse(url)

    if parsed.netloc.lower() not in {
        "open.spotify.com",
        "www.open.spotify.com"
    }:
        raise ValueError(
            "La URL no parece ser de Spotify."
        )

    parts = [
        part
        for part in parsed.path.split("/")
        if part
    ]

    if (
        len(parts) < 2
        or parts[0].lower() != "playlist"
    ):
        raise ValueError(
            "La URL debe apuntar a una playlist de Spotify."
        )

    playlist_id = parts[1].strip()

    if not re.fullmatch(
        r"[A-Za-z0-9]+",
        playlist_id
    ):
        raise ValueError(
            "ID de playlist de Spotify no válido."
        )

    embed_url = (
        "https://open.spotify.com/embed/playlist/"
        + playlist_id
    )

    response = requests.get(
        embed_url,
        timeout=20,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(X11; Linux x86_64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/151.0 Safari/537.36"
            ),
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8"
        }
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    # Nombre de la playlist y propietario.
    metadata_container = soup.select_one(
        ".TrackListWidget_metadataContainer__EP9LF"
    )

    playlist_name = ""

    if metadata_container:
        spans = metadata_container.select(
            "span"
        )

        for span in spans:
            value = span.get_text(
                " ",
                strip=True
            )

            if value:
                if value.lower() not in {
                    "·"
                }:
                    playlist_name = value
                    break

    if not playlist_name:
        title_element = soup.select_one(
            "title"
        )

        if title_element:
            playlist_name = title_element.get_text(
                " ",
                strip=True
            )

    if playlist_name and " · " in playlist_name:
        playlist_name = playlist_name.split(
            " · ",
            1
        )[0].strip()

    if not playlist_name:
        playlist_name = (
            "Playlist Spotify "
            + playlist_id
        )

    rows = soup.select(
        'li[data-testid^="tracklist-row-"]'
    )

    if not rows:
        raise ValueError(
            "Spotify no ha devuelto canciones "
            "para esta playlist."
        )

    tracks = []

    for row in rows:

        title_element = row.select_one(
            "h3"
        )

        artist_element = row.select_one(
            "h4"
        )

        duration_element = row.select_one(
            'div[data-testid="duration-cell"]'
        )

        title = (
            title_element.get_text(
                " ",
                strip=True
            )
            if title_element
            else ""
        )

        artist = (
            artist_element.get_text(
                " ",
                strip=True
            )
            if artist_element
            else ""
        )

        duration_text = (
            duration_element.get_text(
                " ",
                strip=True
            )
            if duration_element
            else ""
        )

        duration = 0

        try:

            parts = duration_text.split(":")

            if len(parts) == 2:

                minutes = int(parts[0])
                seconds = int(parts[1])

                duration = (
                    minutes * 60
                    + seconds
                )

            elif len(parts) == 3:

                hours = int(parts[0])
                minutes = int(parts[1])
                seconds = int(parts[2])

                duration = (
                    hours * 3600
                    + minutes * 60
                    + seconds
                )

        except Exception:
            duration = 0

        if not title:
            continue

        tracks.append({
            "title": title,
            "artist": artist,
            "album": "",
            "duration": duration
        })

    if not tracks:
        raise ValueError(
            "No se encontraron canciones "
            "en la playlist de Spotify."
        )

    return {
        "name": playlist_name,
        "count": len(tracks),
        "tracks": tracks,
        "url": (
            "https://open.spotify.com/playlist/"
            + playlist_id
        ),
        "spotify_url": (
            "https://open.spotify.com/playlist/"
            + playlist_id
        )
    }


def spotify_parse_playlist(filename, content):

    name = Path(
        filename or "playlist"
    ).stem

    extension = Path(
        filename or ""
    ).suffix.lower()

    if extension == ".csv":
        tracks = spotify_parse_csv(content)

    elif extension == ".json":
        tracks = spotify_parse_json(content)

    elif extension == ".txt":
        tracks = spotify_parse_txt(content)

    else:

        raise ValueError(
            "Formato no compatible. "
            "Usa CSV, TXT o JSON."
        )

    if not tracks:

        raise ValueError(
            "No se encontraron canciones "
            "en el archivo."
        )

    return {
        "name": name,
        "count": len(tracks),
        "tracks": tracks
    }


def spotify_search_and_queue(tracks):

    queued = []
    errors = []

    for index, track in enumerate(tracks, 1):

        title = _spotify_clean(
            track.get("title")
        )

        artist = _spotify_clean(
            track.get("artist")
        )

        if not title:
            errors.append({
                "position": index,
                "title": "",
                "error": "Canción sin título"
            })
            continue

        if artist:
            query = f"{artist} - {title}"
        else:
            query = title

        try:

            results = search_youtube(query)

            if not results:

                raise RuntimeError(
                    "No se encontró en YouTube"
                )

            result = results[0]

            video_id = result.get("id")

            if not video_id:

                raise RuntimeError(
                    "YouTube no devolvió un ID"
                )

            job = os.urandom(8).hex()

            display_title = (
                f"{artist} - {title}"
                if artist
                else title
            )

            downloads[job] = {
                "status": "queued",
                "message": "En cola...",
                "progress": 0,
                "title": display_title,
                "id": video_id,
                "type": "navidrome"
            }

            download_queue.put(
                (
                    job,
                    video_id,
                    display_title
                )
            )

            queued.append({
                "job": job,
                "position": index,
                "title": display_title,
                "id": video_id
            })

        except Exception as error:

            print(
                "ERROR Spotify:",
                repr(error)
            )

            errors.append({
                "position": index,
                "title": (
                    f"{artist} - {title}"
                    if artist
                    else title
                ),
                "error": str(error)
            })

    return {
        "queued": queued,
        "errors": errors,
        "count": len(queued)
    }



def do_download_mobile(
        job,
        video_id,
        title="",
        album_group=None,
        album_title=None,
        album_track_index=0,
        album_track_total=0,
        format_name="mp3"):

    with download_lock:

        process = None

        format_name = str(
            format_name or "mp3"
        ).lower().strip()

        if format_name not in ("mp3", "opus"):
            format_name = "mp3"

        job_dir = MOBILE_DOWNLOAD_DIR / job
        job_dir.mkdir(parents=True, exist_ok=True)

        try:

            downloads[job] = {
                "status": "running",
                "message": "Descargando para móvil...",
                "progress": 0,
                "title": title or video_id,
                "id": video_id,
                "type": "mobile",
                "album_group": album_group,
                "album_title": album_title,
                "album_track_index": album_track_index,
                "album_track_total": album_track_total
            }

            save_mobile_downloads_state()

            url = (
                f"https://www.youtube.com/watch?v={video_id}"
            )

            if format_name == "opus":

                command = [

                    "yt-dlp",

                    "--js-runtimes",
                    "node",

                    "--newline",

                    "--progress-template",
                    "%(progress._percent_str)s",

                    "--no-overwrites",

                    "--continue",

                    "-f",
                    "bestaudio[acodec^=opus]",

                    "-x",

                    "--audio-format",
                    "opus",

                    "--audio-quality",
                    "0",

                    "--embed-thumbnail",

                    "--add-metadata",

                    "--parse-metadata",
                    "%(channel)s:%(artist)s",

                    "-o",

                    str(
                        job_dir /
                        "%(title)s.%(ext)s"
                    ),

                    url
                ]

            else:

                ffmpeg_progress_file = (
                    job_dir /
                    "ffmpeg-progress.log"
                )

                command = [

                    "yt-dlp",

                    "--js-runtimes",
                    "node",

                    "--newline",

                    "--progress-template",
                    "%(progress._percent_str)s",

                    "--no-overwrites",

                    "--continue",

                    "-x",

                    "--audio-format",
                    "mp3",

                    "--audio-quality",
                    "0",

                    "--postprocessor-args",
                    (
                        "ExtractAudio:"
                        "-progress "
                        + str(ffmpeg_progress_file)
                    ),

                    "--embed-thumbnail",

                    "--add-metadata",

                    "--parse-metadata",
                    "%(channel)s:%(artist)s",

                    "-o",

                    str(
                        job_dir /
                        "%(title)s.%(ext)s"
                    ),

                    url
                ]

            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            active_processes[job] = process

            output_lines = []

            start_time = time.time()

            # -------------------------------------------------
            # Monitor independiente del progreso real de FFmpeg.
            # Solo se utiliza durante la conversión MP3.
            # -------------------------------------------------

            ffmpeg_progress_stop = threading.Event()

            def monitor_mp3_conversion():

                if format_name != "mp3":
                    return

                progress_file = Path(
                    ffmpeg_progress_file
                )

                duration = 0.0
                last_progress = -1
                last_save = 0.0

                while not ffmpeg_progress_stop.is_set():

                    try:

                        # Buscar la fuente WebM descargada.
                        if duration <= 0:

                            webm_files = list(
                                job_dir.glob("*.webm")
                            )

                            if webm_files:

                                try:

                                    probe = subprocess.run(
                                        [
                                            "ffprobe",
                                            "-v",
                                            "error",
                                            "-show_entries",
                                            "format=duration",
                                            "-of",
                                            "default=noprint_wrappers=1:nokey=1",
                                            str(webm_files[0])
                                        ],
                                        capture_output=True,
                                        text=True,
                                        timeout=10
                                    )

                                    value = (
                                        probe.stdout or ""
                                    ).strip()

                                    if value:
                                        duration = float(value)

                                except Exception:
                                    pass

                        if progress_file.exists():

                            values = {}

                            text = progress_file.read_text(
                                encoding="utf-8",
                                errors="ignore"
                            )

                            for raw_line in text.splitlines():

                                if "=" not in raw_line:
                                    continue

                                key, value = raw_line.split(
                                    "=",
                                    1
                                )

                                values[key.strip()] = (
                                    value.strip()
                                )

                            seconds = 0.0

                            out_time_ms = values.get(
                                "out_time_ms"
                            )

                            out_time = values.get(
                                "out_time"
                            )

                            if out_time_ms:

                                try:

                                    seconds = (
                                        float(out_time_ms)
                                        / 1000000.0
                                    )

                                except Exception:
                                    seconds = 0.0

                            if seconds <= 0 and out_time:

                                try:

                                    parts = out_time.split(":")

                                    if len(parts) == 3:

                                        seconds = (
                                            float(parts[0])
                                            * 3600
                                            +
                                            float(parts[1])
                                            * 60
                                            +
                                            float(parts[2])
                                        )

                                except Exception:
                                    seconds = 0.0

                            if (
                                duration > 0
                                and seconds >= 0
                            ):

                                percent = round(
                                    min(
                                        99,
                                        max(
                                            0,
                                            (
                                                seconds
                                                / duration
                                            ) * 100
                                        )
                                    )
                                )

                                if percent != last_progress:

                                    downloads[job][
                                        "status"
                                    ] = "processing"

                                    downloads[job][
                                        "progress"
                                    ] = percent

                                    downloads[job][
                                        "message"
                                    ] = (
                                        "⚙️ "
                                        "Convirtiendo "
                                        "audio a MP3... "
                                        + str(percent)
                                        + "%"
                                    )

                                    last_progress = percent

                                    now = time.time()

                                    if (
                                        now - last_save
                                        >= 2
                                    ):

                                        save_mobile_downloads_state()
                                        last_save = now

                    except Exception:
                        pass

                    ffmpeg_progress_stop.wait(0.5)

            mp3_progress_thread = threading.Thread(
                target=monitor_mp3_conversion,
                daemon=True
            )

            if format_name == "mp3":
                mp3_progress_thread.start()

            while True:

                line = process.stdout.readline()

                if line:

                    output_lines.append(line)

                    if len(output_lines) > 100:
                        output_lines.pop(0)

                    # -------------------------------------------------
                    # Progreso real del trabajo móvil.
                    #
                    # El porcentaje de yt-dlp llega al 100% cuando termina
                    # la descarga, pero todavía puede quedar bastante
                    # procesamiento de ffmpeg/yt-dlp:
                    #
                    #   ExtractAudio
                    #   Metadata
                    #   ThumbnailsConvertor
                    #   EmbedThumbnail
                    #
                    # No dejamos que la APK parezca bloqueada al 100%.
                    # -------------------------------------------------

                    processing_message = None

                    if "[ExtractAudio]" in line:

                        if format_name == "opus":
                            processing_message = (
                                "⚙️ Preparando audio Opus..."
                            )
                        else:
                            processing_message = (
                                "⚙️ Convirtiendo audio a MP3..."
                            )

                    elif "[Metadata]" in line:
                        processing_message = (
                            "🏷️ Añadiendo metadatos..."
                        )

                    elif (
                        "[ThumbnailsConvertor]" in line
                        or "[EmbedThumbnail]" in line
                    ):
                        processing_message = (
                            "🖼️ Preparando portada..."
                        )

                    if processing_message is not None:

                        downloads[job]["status"] = "processing"

                        # Durante la conversión MP3 el monitor
                        # independiente escribe el porcentaje real.
                        # No mostrar 100% hasta que termine.
                        if not (
                            format_name == "mp3"
                            and processing_message
                            == "⚙️ Convirtiendo audio a MP3..."
                        ):
                            downloads[job]["progress"] = 100

                        downloads[job]["message"] = (
                            processing_message
                        )

                        save_mobile_downloads_state()

                    else:

                        match = re.search(
                            r"(\d+(?:\.\d+)?)%",
                            line
                        )

                        if match:

                            try:

                                percent = float(
                                    match.group(1)
                                )

                                downloads[job]["status"] = "running"

                                downloads[job]["progress"] = min(
                                    100,
                                    max(0, round(percent))
                                )

                                downloads[job]["message"] = (
                                    "Descargando para móvil... "
                                    + str(
                                        downloads[job]["progress"]
                                    )
                                    + "%"
                                )

                                now = time.time()

                                if now - last_state_save >= 5:
                                    save_mobile_downloads_state()
                                    last_state_save = now

                            except Exception:
                                pass

                elif process.poll() is not None:

                    break

                pause_started = None

                with download_control_lock:
                    pause_started = download_pause_started.get(job)

                paused_elapsed = 0

                if pause_started is not None:
                    paused_elapsed = time.time() - pause_started

                active_time = (
                    time.time()
                    - start_time
                    - paused_elapsed
                )

                if active_time > 600:

                    process.kill()

                    raise RuntimeError(
                        "Tiempo de descarga móvil agotado."
                    )

            ffmpeg_progress_stop.set()

            if format_name == "mp3":

                try:
                    mp3_progress_thread.join(
                        timeout=2
                    )
                except Exception:
                    pass

            returncode = process.wait()

            if returncode != 0:

                raise RuntimeError(
                    "".join(output_lines)[-2000:]
                    or
                    "Error durante la descarga móvil"
                )

            audio_files = sorted(
                (
                    job_dir.glob("*.mp3")
                    if format_name == "mp3"
                    else job_dir.glob("*.opus")
                ),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )

            if not audio_files:

                raise RuntimeError(
                    "No se encontró el archivo "
                    + format_name.upper()
                    + " móvil descargado."
                )

            audio_file = audio_files[0]

            downloads[job] = {
                "status": "done",
                "message": "Canción preparada para el móvil",
                "progress": 100,
                "title": title or video_id,
                "id": video_id,
                "type": "mobile",
                "album_group": album_group,
                "album_title": album_title,
                "album_track_index": album_track_index,
                "album_track_total": album_track_total,
                "format": format_name,
                "file": str(audio_file),
                "filename": audio_file.name,
                "size": audio_file.stat().st_size
            }

            save_mobile_downloads_state()

            add_history(
                job,
                video_id,
                title
            )

            active_processes.pop(
                job,
                None
            )

            clear_download_control(job)

        except Exception as error:

            active_processes.pop(
                job,
                None
            )

            control = get_download_control(job)

            if control == "cancelled":

                downloads[job] = {
                    "status": "cancelled",
                    "message": "Descarga cancelada.",
                    "progress": downloads.get(
                        job,
                        {}
                    ).get(
                        "progress",
                        0
                    ),
                    "title": title or video_id,
                    "id": video_id,
                    "type": "mobile",
                    "album_group": album_group,
                    "album_title": album_title,
                    "album_track_index": album_track_index,
                    "album_track_total": album_track_total
                }

            else:

                downloads[job] = {
                    "status": "error",
                    "message": str(error),
                    "progress": 0,
                    "title": title or video_id,
                    "id": video_id,
                    "type": "mobile",
                    "album_group": album_group,
                    "album_title": album_title,
                    "album_track_index": album_track_index,
                    "album_track_total": album_track_total
                }

            save_mobile_downloads_state()

            add_history(
                job,
                video_id,
                title
            )

            clear_download_control(job)


class Handler(BaseHTTPRequestHandler):

    def send_json(self, data, status=200):

        raw = json.dumps(
            data,
            ensure_ascii=False
        ).encode("utf-8")

        self.send_response(status)

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )

        self.send_header(
            "Content-Length",
            str(len(raw))
        )

        self.send_header(
            "Access-Control-Allow-Origin",
            "*"
        )

        self.end_headers()

        self.wfile.write(raw)

    def do_GET(self):

        parsed = urllib.parse.urlparse(
            self.path
        )

        if parsed.path == "/":

            raw = HTML.encode("utf-8")

            self.send_response(200)

            self.send_header(
                "Content-Type",
                "text/html; charset=utf-8"
            )

            self.send_header(
                "Content-Length",
                str(len(raw))
            )

            self.end_headers()

            self.wfile.write(raw)

            return


        if parsed.path == "/api/album-track":

            params = urllib.parse.parse_qs(
                parsed.query
            )

            query = params.get(
                "q",
                [""]
            )[0].strip()

            if not query:
                self.send_json(
                    {
                        "error": "Escribe una canción."
                    },
                    400
                )
                return

            try:

                results = search_youtube(query)

                if not results:
                    self.send_json(
                        {
                            "error":
                                "No se encontró la canción en YouTube."
                        },
                        404
                    )
                    return

                result = results[0]

                self.send_json(
                    {
                        "id": result.get("id", ""),
                        "title": result.get("title", ""),
                        "thumbnail": result.get("thumbnail", ""),
                        "duration": result.get("duration", 0),
                        "channel": result.get(
                            "channel",
                            result.get("uploader", "")
                        )
                    }
                )

            except Exception as error:

                print(
                    "ERROR /api/album-track:",
                    repr(error)
                )

                self.send_json(
                    {
                        "error": str(error)
                    },
                    500
                )

            return


        if parsed.path == "/api/album":

            params = urllib.parse.parse_qs(
                parsed.query
            )

            album_id = params.get(
                "id",
                [""]
            )[0].strip()

            query = params.get(
                "q",
                [""]
            )[0].strip()

            # ---------------------------------------------
            # Cargar álbum seleccionado
            # ---------------------------------------------
            if album_id:

                try:

                    album_title = params.get(
                        "title",
                        [""]
                    )[0].strip()

                    artist = params.get(
                        "artist",
                        [""]
                    )[0].strip()

                    result = load_album_by_release_group(
                        album_id,
                        album_title,
                        artist
                    )

                    self.send_json(
                        result
                    )

                except Exception as error:

                    print(
                        "ERROR /api/album id:",
                        repr(error)
                    )

                    self.send_json(
                        {
                            "error": str(error)
                        },
                        500
                    )

                return

            # ---------------------------------------------
            # Buscar álbum o artista
            # ---------------------------------------------
            if not query:

                self.send_json(
                    {
                        "error":
                            "Escribe un álbum o artista."
                    },
                    400
                )

                return

            try:

                result = search_album_backend(
                    query
                )

                self.send_json(
                    result
                )

            except Exception as error:

                print(
                    "ERROR /api/album:",
                    repr(error)
                )

                self.send_json(
                    {
                        "error": str(error)
                    },
                    500
                )

            return


            params = urllib.parse.parse_qs(
                parsed.query
            )

            query = params.get(
                "q",
                [""]
            )[0].strip()

            if not query:

                self.send_json(
                    {
                        "error":
                            "Escribe un álbum o artista."
                    },
                    400
                )

                return

            try:

                result = search_album_backend(
                    query
                )

                self.send_json(
                    result
                )

            except Exception as error:

                print(
                    "ERROR /api/album:",
                    repr(error)
                )

                self.send_json(
                    {
                        "error":
                            str(error)
                    },
                    500
                )

            return

        if parsed.path == "/api/search":

            params = urllib.parse.parse_qs(
                parsed.query
            )

            query = params.get(
                "q",
                [""]
            )[0].strip()

            if not query:

                self.send_json(
                    {
                        "error":
                            "Escribe algo para buscar."
                    },
                    400
                )

                return

            try:

                results = search_youtube(query)

                self.send_json(
                    {
                        "results": results
                    }
                )

            except Exception as error:

                self.send_json(
                    {
                        "error": str(error)
                    },
                    500
                )

            return

        if parsed.path == "/api/preview":

            params = urllib.parse.parse_qs(
                parsed.query
            )

            video_id = params.get(
                "id",
                [""]
            )[0].strip()

            if not video_id:

                self.send_json(
                    {
                        "error":
                            "Falta el ID del vídeo."
                    },
                    400
                )

                return

            try:

                preview_url = get_preview_url(video_id)

                self.send_json(
                    {
                        "url": preview_url
                    }
                )

            except Exception as error:

                self.send_json(
                    {
                        "error": str(error)
                    },
                    500
                )

            return

        if parsed.path == "/api/spotify/lists":

            self.send_json({
                "lists": load_spotify_lists()
            })

            return


        if parsed.path == "/api/queue":

            queued = []

            active_states = (
                "queued",
                "running",
                "paused",
                "downloading",
                "processing"
            )

            for job, item in downloads.items():

                if item.get("status") in active_states:

                    entry = {
                        "job": job,
                        **item
                    }

                    if not entry.get("type"):
                        entry["type"] = "navidrome"

                    queued.append(entry)

            history_response = []

            for item in history[-50:]:

                entry = dict(item)

                if not entry.get("type"):
                    entry["type"] = "navidrome"

                history_response.append(entry)

            self.send_json({
                "queue": queued,
                "history": history_response
            })

            return


        if parsed.path == "/api/pause":

            params = urllib.parse.parse_qs(
                parsed.query
            )

            job = params.get(
                "id",
                [""]
            )[0].strip()

            if not job or job not in downloads:

                self.send_json(
                    {
                        "error": "Trabajo no encontrado."
                    },
                    404
                )

                return

            ok, message = pause_download(job)

            self.send_json(
                {
                    "ok": ok,
                    "job": job,
                    "message": message,
                    "status": downloads.get(
                        job,
                        {}
                    ).get(
                        "status",
                        ""
                    )
                },
                200 if ok else 400
            )

            return


        if parsed.path == "/api/resume":

            params = urllib.parse.parse_qs(
                parsed.query
            )

            job = params.get(
                "id",
                [""]
            )[0].strip()

            if not job or job not in downloads:

                self.send_json(
                    {
                        "error": "Trabajo no encontrado."
                    },
                    404
                )

                return

            ok, message = resume_download(job)

            self.send_json(
                {
                    "ok": ok,
                    "job": job,
                    "message": message,
                    "status": downloads.get(
                        job,
                        {}
                    ).get(
                        "status",
                        ""
                    )
                },
                200 if ok else 400
            )

            return


        if parsed.path == "/api/cancel":

            params = urllib.parse.parse_qs(
                parsed.query
            )

            job = params.get(
                "id",
                [""]
            )[0].strip()

            if not job or job not in downloads:

                self.send_json(
                    {
                        "error": "Trabajo no encontrado."
                    },
                    404
                )

                return

            ok, message = cancel_download(job)

            self.send_json(
                {
                    "ok": ok,
                    "job": job,
                    "message": message,
                    "status": downloads.get(
                        job,
                        {}
                    ).get(
                        "status",
                        ""
                    )
                },
                200 if ok else 400
            )

            return


        if parsed.path == "/api/downloads/delete":
            params = urllib.parse.parse_qs(parsed.query)
            job = params.get("id", [""])[0].strip()

            if not job:
                self.send_json(
                    {"ok": False, "error": "Trabajo no especificado."},
                    400
                )
                return

            original_count = len(history)

            history[:] = [
                item
                for item in history
                if item.get("job") != job
            ]

            if len(history) == original_count:
                self.send_json(
                    {
                        "ok": False,
                        "job": job,
                        "message": "Registro no encontrado."
                    },
                    404
                )
                return

            save_history()

            self.send_json(
                {
                    "ok": True,
                    "job": job,
                    "message": "Registro eliminado."
                }
            )
            return


        if parsed.path == "/api/download-mobile-file":

            params = urllib.parse.parse_qs(
                parsed.query
            )

            job = params.get(
                "id",
                [""]
            )[0]

            info = downloads.get(job)

            if not info:

                self.send_json({
                    "error": "Trabajo no encontrado."
                }, 404)

                return

            if info.get("type") != "mobile":

                self.send_json({
                    "error": "El trabajo no es una descarga móvil."
                }, 400)

                return

            if info.get("status") != "done":

                self.send_json({
                    "error": "La descarga todavía no está terminada."
                }, 409)

                return

            file_value = info.get("file")

            if not file_value:

                self.send_json({
                    "error": "El archivo móvil no está disponible."
                }, 404)

                return

            try:

                mobile_root = MOBILE_DOWNLOAD_DIR.resolve()
                file_path = Path(file_value).resolve()

                if (
                    mobile_root not in file_path.parents
                    or not file_path.is_file()
                ):

                    raise RuntimeError(
                        "Archivo móvil no válido."
                    )

                size = file_path.stat().st_size

                suffix = file_path.suffix.lower()

                if suffix == ".opus":
                    content_type = "audio/ogg"
                    download_name = "download.opus"
                else:
                    content_type = "audio/mpeg"
                    download_name = "download.mp3"

                self.send_response(200)

                self.send_header(
                    "Content-Type",
                    content_type
                )

                self.send_header(
                    "Content-Length",
                    str(size)
                )

                self.send_header(
                    "Content-Disposition",
                    'attachment; filename="' + download_name + '"'
                )

                self.send_header(
                    "Access-Control-Allow-Origin",
                    "*"
                )

                self.end_headers()

                with file_path.open("rb") as source:

                    while True:

                        chunk = source.read(1024 * 1024)

                        if not chunk:
                            break

                        self.wfile.write(chunk)

                return

            except BrokenPipeError:

                return

            except Exception as error:

                print(
                    "ERROR /api/download-mobile-file:",
                    repr(error)
                )

                return


        if parsed.path == "/api/downloads":

            mobile_downloads = []

            for job, item in downloads.items():

                if item.get("type") == "mobile":

                    mobile_downloads.append({
                        "job": job,
                        **item
                    })

            self.send_json({
                "downloads": mobile_downloads
            })

            return


        if parsed.path == "/api/status":

            params = urllib.parse.parse_qs(
                parsed.query
            )

            job = params.get(
                "id",
                [""]
            )[0]

            self.send_json(
                downloads.get(
                    job,
                    {
                        "status": "unknown",
                        "message":
                            "Trabajo no encontrado."
                    }
                )
            )

            return

        self.send_error(404)

    def do_POST(self):

        parsed = urllib.parse.urlparse(
            self.path
        )

        if parsed.path == "/api/history/clear":

            history.clear()

            save_history()

            self.send_json({
                "ok": True
            })

            return



        if parsed.path == "/api/downloads/reset":

            active_jobs = []

            active_states = (
                "queued",
                "running",
                "paused",
                "downloading",
                "processing"
            )

            for job, item in downloads.items():

                if item.get("status") in active_states:
                    active_jobs.append(job)

            if active_jobs:

                self.send_json({
                    "ok": False,
                    "error":
                        "No se puede resetear el gestor mientras "
                        "hay descargas activas.",
                    "active": active_jobs
                }, 409)

                return


            history_count = len(history)

            history.clear()

            save_history()


            mobile_jobs = []

            for job, item in list(downloads.items()):

                if item.get("type") == "mobile":
                    mobile_jobs.append(job)

            for job in mobile_jobs:
                downloads.pop(job, None)

            save_mobile_downloads_state()


            self.send_json({
                "ok": True,
                "message":
                    "Gestor de descargas reseteado.",
                "history_reset": history_count,
                "mobile_reset": len(mobile_jobs)
            })

            return


        if parsed.path == "/api/spotify/list/load":

            length = int(
                self.headers.get(
                    "Content-Length",
                    0
                )
            )

            body = self.rfile.read(length)

            try:

                data = json.loads(body)

                playlist_id = str(
                    data.get("id", "")
                )

                lists = load_spotify_lists()

                selected = None

                for playlist in lists:
                    if str(
                        playlist.get("id", "")
                    ) == playlist_id:
                        selected = playlist
                        break

                if selected is None:
                    raise ValueError(
                        "Playlist no encontrada."
                    )

                self.send_json({
                    "ok": True,
                    "playlist": selected
                })

            except Exception as error:

                print(
                    "ERROR /api/spotify/list/load:",
                    repr(error)
                )

                self.send_json({
                    "error": str(error)
                }, 400)

            return


        if parsed.path == "/api/spotify/list/delete":

            length = int(
                self.headers.get(
                    "Content-Length",
                    0
                )
            )

            body = self.rfile.read(length)

            try:

                data = json.loads(body)

                playlist_id = str(
                    data.get("id", "")
                )

                lists = load_spotify_lists()

                new_lists = [
                    playlist
                    for playlist in lists
                    if str(
                        playlist.get("id", "")
                    ) != playlist_id
                ]

                if len(new_lists) == len(lists):
                    raise ValueError(
                        "Playlist no encontrada."
                    )

                save_spotify_lists(new_lists)

                self.send_json({
                    "ok": True
                })

            except Exception as error:

                print(
                    "ERROR /api/spotify/list/delete:",
                    repr(error)
                )

                self.send_json({
                    "error": str(error)
                }, 400)

            return


        if parsed.path == "/api/spotify/import":

            length = int(
                self.headers.get(
                    "Content-Length",
                    0
                )
            )

            body = self.rfile.read(length)

            try:

                data = json.loads(body)

                spotify_url = (
                    data.get("url")
                    or data.get("spotify_url")
                    or ""
                ).strip()

                if spotify_url:

                    result = spotify_parse_spotify_url(
                        spotify_url
                    )

                else:

                    filename = data.get(
                        "filename",
                        "playlist.txt"
                    )

                    content = data.get(
                        "content",
                        ""
                    )

                    if not content:
                        raise ValueError(
                            "El archivo está vacío."
                        )

                    result = spotify_parse_playlist(
                        filename,
                        content
                    )

                saved_playlist = save_spotify_playlist(
                    result
                )

                if saved_playlist:
                    result["saved_id"] = (
                        saved_playlist.get("id")
                    )

                self.send_json(
                    result
                )

            except Exception as error:

                print(
                    "ERROR /api/spotify/import:",
                    repr(error)
                )

                self.send_json(
                    {
                        "error": str(error)
                    },
                    400
                )

            return


        if parsed.path == "/api/spotify/download":

            length = int(
                self.headers.get(
                    "Content-Length",
                    0
                )
            )

            body = self.rfile.read(length)

            try:

                data = json.loads(body)

                tracks = data.get(
                    "tracks",
                    []
                )

                if not isinstance(
                    tracks,
                    list
                ) or not tracks:

                    raise ValueError(
                        "No hay canciones seleccionadas."
                    )

                # Limitamos la petición para evitar
                # una carga accidental enorme.
                if len(tracks) > 500:

                    raise ValueError(
                        "La playlist supera el límite de 500 canciones por tanda."
                    )

                result = spotify_search_and_queue(
                    tracks
                )

                self.send_json(
                    result
                )

            except Exception as error:

                print(
                    "ERROR /api/spotify/download:",
                    repr(error)
                )

                self.send_json(
                    {
                        "error": str(error)
                    },
                    400
                )

            return


        if parsed.path == "/api/download-mobile":

            length = int(
                self.headers.get(
                    "Content-Length",
                    0
                )
            )

            body = self.rfile.read(length)

            try:

                data = json.loads(body)

                video_id = data.get("id")

                if not video_id:

                    raise ValueError(
                        "Falta el ID del vídeo."
                    )

                job = os.urandom(8).hex()

                title = data.get(
                    "title",
                    video_id
                )

                album_group = data.get(
                    "album_group"
                )

                album_title = data.get(
                    "album_title"
                )

                album_track_index = data.get(
                    "album_track_index",
                    0
                )

                album_track_total = data.get(
                    "album_track_total",
                    0
                )

                format_name = str(
                    data.get("format", "mp3")
                    or "mp3"
                ).lower().strip()

                if format_name not in ("mp3", "opus"):
                    format_name = "mp3"

                downloads[job] = {
                    "status": "queued",
                    "message": "En cola para móvil...",
                    "progress": 0,
                    "title": title,
                    "id": video_id,
                    "type": "mobile",
                    "album_group": album_group,
                    "album_title": album_title,
                    "album_track_index": album_track_index,
                    "album_track_total": album_track_total,
                    "format": format_name
                }

                save_mobile_downloads_state()

                download_queue.put(
                    (
                        job,
                        video_id,
                        title,
                        "mobile",
                        album_group,
                        album_title,
                        album_track_index,
                        album_track_total,
                        format_name
                    )
                )

                self.send_json({
                    "job": job,
                    "type": "mobile"
                })

            except Exception as error:

                self.send_json(
                    {
                        "error": str(error)
                    },
                    400
                )

            return


        if parsed.path != "/api/download":

            self.send_error(404)

            return

        length = int(
            self.headers.get(
                "Content-Length",
                0
            )
        )

        body = self.rfile.read(length)

        try:

            data = json.loads(body)

            video_id = data.get("id")

            if not video_id:

                raise ValueError(
                    "Falta el ID del vídeo."
                )

            job = os.urandom(8).hex()

            title = data.get(
                "title",
                video_id
            )

            album_group = data.get(
                "album_group"
            )

            album_title = data.get(
                "album_title"
            )

            album_track_index = data.get(
                "album_track_index",
                0
            )

            album_track_total = data.get(
                "album_track_total",
                0
            )

            downloads[job] = {
                "status": "queued",
                "message": "En cola...",
                "progress": 0,
                "title": title,
                "id": video_id,
                "type": "navidrome",
                "album_group": album_group,
                "album_title": album_title,
                "album_track_index": album_track_index,
                "album_track_total": album_track_total
            }

            download_queue.put(
                (
                    job,
                    video_id,
                    title,
                    album_group,
                    album_title,
                    album_track_index,
                    album_track_total
                )
            )

            self.send_json(
                {
                    "job": job
                }
            )

        except Exception as error:

            self.send_json(
                {
                    "error": str(error)
                },
                400
            )


if __name__ == "__main__":

    print(
        f"Music Downloader escuchando "
        f"en http://{HOST}:{PORT}"
    )

    server = ThreadingHTTPServer(
        (HOST, PORT),
        Handler
    )

    server.serve_forever()
