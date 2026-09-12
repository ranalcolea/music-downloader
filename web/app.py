#!/usr/bin/env python3

import json
import os
import subprocess
import threading
import urllib.parse
import urllib.request
import queue
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "0.0.0.0"
PORT = 8080

BASE_DIR = Path("/opt/music-downloader")
DOWNLOAD_DIR = BASE_DIR / "downloads"
TAG_SCRIPT = BASE_DIR / "tag-music.py"

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

downloads = {}
download_lock = threading.Lock()

download_queue = queue.Queue()
active_processes = {}

HISTORY_FILE = BASE_DIR / "download-history.json"

history = []

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

    history.append({
        "job": job,
        "id": video_id,
        "title": title or video_id,
        "time": time.strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "status": downloads.get(
            job,
            {}
        ).get(
            "status",
            "done"
        )
    })

    save_history()


def queue_worker():

    while True:

        job, video_id, title = download_queue.get()

        try:

            do_download(
                job,
                video_id,
                title
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
        f"ytsearch50:{query}"
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=90
    )

    if result.returncode != 0:

        raise RuntimeError(
            result.stderr.strip()
            or "yt-dlp ha fallado"
        )

    data = json.loads(result.stdout)

    entries = data.get("entries", [])

    output = []

    for item in entries:

        if not item:
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

    return output


def get_preview_url(video_id):

    url = (
        f"https://www.youtube.com/watch?v={video_id}"
    )

    command = [

        "yt-dlp",

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


def do_download(job, video_id, title=""):

    with download_lock:

        process = None


        try:

            downloads[job] = {
                "status": "running",
                "message": "Descargando...",
                "progress": 0,
                "title": title or video_id,
                "id": video_id
            }

            url = (
                f"https://www.youtube.com/watch?v={video_id}"
            )

            command = [

                "yt-dlp",

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

                if time.time() - start_time > 600:

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
                "id": video_id
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

        except Exception as error:

            active_processes.pop(
                job,
                None
            )

            downloads[job] = {
                "status": "error",
                "message": str(error),
                "progress": 0,
                "title": title or video_id,
                "id": video_id
            }

            add_history(
                job,
                video_id,
                title
            )




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

        if parsed.path == "/api/queue":

            queued = []

            for job, item in downloads.items():

                if item.get("status") in (
                    "queued",
                    "running"
                ):

                    queued.append({
                        "job": job,
                        **item
                    })

            self.send_json({
                "queue": queued,
                "history": history[-50:]
            })

            return

        if parsed.path == "/api/cancel":

            params = urllib.parse.parse_qs(
                parsed.query
            )

            job = params.get(
                "id",
                [""]
            )[0].strip()

            process = active_processes.get(job)

            if not process:

                self.send_json({
                    "error": "La descarga no está activa."
                }, 400)

                return

            try:

                process.kill()

                downloads[job] = {
                    "status": "cancelled",
                    "message": "Descarga cancelada",
                    "progress": downloads.get(
                        job,
                        {}
                    ).get(
                        "progress",
                        0
                    ),
                    "title": downloads.get(
                        job,
                        {}
                    ).get(
                        "title",
                        job
                    ),
                    "id": downloads.get(
                        job,
                        {}
                    ).get(
                        "id",
                        ""
                    )
                }

                add_history(
                    job,
                    downloads[job]["id"],
                    downloads[job]["title"]
                )

                self.send_json({
                    "ok": True,
                    "message": "Descarga cancelada"
                })

            except Exception as error:

                self.send_json({
                    "error": str(error)
                }, 500)

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

            downloads[job] = {
                "status": "queued",
                "message": "En cola...",
                "progress": 0,
                "title": title,
                "id": video_id
            }

            download_queue.put(
                (
                    job,
                    video_id,
                    title
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
