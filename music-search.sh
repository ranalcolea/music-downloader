#!/bin/bash

DOWNLOAD_DIR="/opt/music-downloader/downloads"

mkdir -p "$DOWNLOAD_DIR"

while true; do

    clear

    echo "=========================================="
    echo "          MUSIC DOWNLOADER"
    echo "=========================================="
    echo
    echo "Introduce una búsqueda de YouTube."
    echo "Ejemplo: Dire Straits Sultans of Swing"
    echo
    read -rp "Buscar (Enter para salir): " SEARCH

    if [[ -z "$SEARCH" ]]; then
        exit 0
    fi

    echo
    echo "Buscando..."
    echo

    mapfile -t RESULTS < <(
        yt-dlp \
            --flat-playlist \
            --print "%(id)s|%(title)s|%(channel)s" \
            "ytsearch10:$SEARCH" 2>/dev/null
    )

    if [[ ${#RESULTS[@]} -eq 0 ]]; then
        echo "No se encontraron resultados."
        read -rp "Pulsa Enter para continuar..."
        continue
    fi

    echo "Resultados:"
    echo

    for i in "${!RESULTS[@]}"; do
        IFS='|' read -r ID TITLE CHANNEL <<< "${RESULTS[$i]}"

        printf "%2d) %s" "$((i + 1))" "$TITLE"

        if [[ -n "$CHANNEL" ]]; then
            printf " — %s" "$CHANNEL"
        fi

        echo
    done

    echo
    read -rp "Selecciona un número (Enter para cancelar): " OPTION

    if [[ -z "$OPTION" ]]; then
        continue
    fi

    if ! [[ "$OPTION" =~ ^[0-9]+$ ]]; then
        echo "Selección no válida."
        sleep 2
        continue
    fi

    INDEX=$((OPTION - 1))

    if (( INDEX < 0 || INDEX >= ${#RESULTS[@]} )); then
        echo "Selección no válida."
        sleep 2
        continue
    fi

    IFS='|' read -r VIDEO_ID VIDEO_TITLE VIDEO_CHANNEL <<< "${RESULTS[$INDEX]}"

    echo
    echo "=========================================="
    echo "SELECCIONADO"
    echo "=========================================="
    echo
    echo "Título : $VIDEO_TITLE"
    echo "Canal  : $VIDEO_CHANNEL"
    echo "ID     : $VIDEO_ID"
    echo

    read -rp "¿Descargar? [S/n]: " CONFIRM

    if [[ "$CONFIRM" =~ ^[Nn]$ ]]; then
        continue
    fi

    echo
    echo "Descargando..."
    echo

    BEFORE_FILE=$(find "$DOWNLOAD_DIR" \
        -type f \
        -name "*.mp3" \
        -printf "%T@|%p\n" 2>/dev/null \
        | sort -nr \
        | head -1 \
        | cut -d'|' -f2-)

    yt-dlp \
        --no-overwrites \
        --continue \
        -x \
        --audio-format mp3 \
        --audio-quality 0 \
        --embed-thumbnail \
        --add-metadata \
        --parse-metadata "%(channel)s:%(artist)s" \
        -o "$DOWNLOAD_DIR/%(artist)s/%(album)s/%(title)s.%(ext)s" \
        "https://www.youtube.com/watch?v=$VIDEO_ID"

    if [[ $? -ne 0 ]]; then
        echo
        echo "ERROR durante la descarga."
        echo
        read -rp "Pulsa Enter para continuar..."
        continue
    fi

    echo
    echo "Descarga completada."
    echo
    echo "Buscando archivo descargado..."
    echo

    DOWNLOADED_FILE=$(find "$DOWNLOAD_DIR" \
        -type f \
        -name "*.mp3" \
        -printf "%T@|%p\n" 2>/dev/null \
        | sort -nr \
        | head -1 \
        | cut -d'|' -f2-)

    if [[ -z "$DOWNLOADED_FILE" || ! -f "$DOWNLOADED_FILE" ]]; then
        echo "No se pudo localizar el MP3."
        echo
        read -rp "Pulsa Enter para continuar..."
        continue
    fi

    echo "Archivo:"
    echo "$DOWNLOADED_FILE"
    echo

    ARTIST=$(ffprobe \
        -v quiet \
        -show_entries format_tags=artist \
        -of default=noprint_wrappers=1:nokey=1 \
        "$DOWNLOADED_FILE" 2>/dev/null)

    if [[ -z "$ARTIST" ]]; then
        ARTIST="$VIDEO_CHANNEL"
    fi

    if [[ -z "$ARTIST" ]]; then
        ARTIST="Desconocido"
    fi

    echo "Artista detectado: $ARTIST"
    echo
    echo "=========================================="
    echo " ORGANIZANDO BIBLIOTECA"
    echo "=========================================="
    echo

    python3 /opt/music-downloader/tag-music.py \
        "$DOWNLOADED_FILE" \
        "$ARTIST"

    TAG_RESULT=$?

    echo

    if [[ $TAG_RESULT -eq 0 ]]; then
        echo "=========================================="
        echo "       ✓ PROCESO COMPLETADO"
        echo "=========================================="
        echo
        echo "La canción ha sido procesada para Navidrome."
    else
        echo "=========================================="
        echo "       ⚠ PROCESAMIENTO CON AVISO"
        echo "=========================================="
        echo
        echo "La descarga se ha conservado."
    fi

    echo
    read -rp "Pulsa Enter para continuar..."

done
