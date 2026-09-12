#!/usr/bin/env python3

import sys
import re
import json
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, APIC
from mutagen.mp3 import MP3


MUSICBRAINZ_URL = "https://musicbrainz.org/ws/2"
COVERART_URL = "https://coverartarchive.org"

USER_AGENT = "RaspberryPi-MusicDownloader/1.0"

NAVIDROME_DIR = Path("/opt/docker/navidrome/music")


def clean_name(name):
    if not name:
        return "Desconocido"

    name = re.sub(r'[<>:"/\\|?*]', "_", str(name))
    name = re.sub(r"\s+", " ", name)
    name = name.strip(" .")

    return name or "Desconocido"


def request_json(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def download_file(url, destination):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        with open(destination, "wb") as output:
            output.write(response.read())


def search_musicbrainz(title, artist):
    query = f'recording:"{title}" AND artist:"{artist}"'

    url = (
        MUSICBRAINZ_URL
        + "/recording/?"
        + urllib.parse.urlencode(
            {
                "query": query,
                "fmt": "json",
                "limit": 5,
                "inc": "artists+releases",
            }
        )
    )

    print()
    print("Buscando en MusicBrainz...")

    try:
        data = request_json(url)

    except urllib.error.HTTPError as error:
        if error.code == 503:
            print("MusicBrainz está temporalmente ocupado.")
            return "TEMP_ERROR"

        print(f"Error HTTP de MusicBrainz: {error.code}")
        return "TEMP_ERROR"

    except Exception as error:
        print(f"Error consultando MusicBrainz: {error}")
        return "TEMP_ERROR"

    recordings = data.get("recordings", [])

    if not recordings:
        return None

    return recordings[0]


def get_release(recording):
    releases = recording.get("releases", [])

    if not releases:
        return None

    releases = sorted(
        releases,
        key=lambda release: (
            not bool(release.get("date")),
            release.get("date", "9999"),
        ),
    )

    return releases[0]


def get_artist(recording):
    artists = recording.get("artist-credit", [])

    names = []

    for item in artists:
        artist = item.get("artist", {})
        name = artist.get("name")

        if name:
            names.append(name)

    return ", ".join(names) or "Desconocido"


def read_original_tags(mp3_path):
    try:
        tags = EasyID3(mp3_path)

        title = tags.get("title", [mp3_path.stem])[0]
        artist = tags.get("artist", ["Desconocido"])[0]
        album = tags.get("album", [""])[0]
        year = tags.get("date", [""])[0]
        genre = tags.get("genre", [""])[0]
        track = tags.get("tracknumber", [""])[0]

        return {
            "title": title,
            "artist": artist,
            "album": album,
            "year": year,
            "genre": genre,
            "track": track,
        }

    except Exception:
        return {
            "title": mp3_path.stem,
            "artist": "Desconocido",
            "album": "",
            "year": "",
            "genre": "",
            "track": "",
        }


def write_tags(mp3_path, metadata):
    try:
        MP3(mp3_path, ID3=ID3)

        tags = EasyID3(mp3_path)

        tags["title"] = metadata["title"]
        tags["artist"] = metadata["artist"]

        if metadata.get("album"):
            tags["album"] = metadata["album"]

        tags["albumartist"] = metadata["artist"]

        if metadata.get("year"):
            tags["date"] = metadata["year"]

        if metadata.get("genre"):
            tags["genre"] = metadata["genre"]

        if metadata.get("track"):
            tags["tracknumber"] = metadata["track"]

        tags.save()

        return True

    except Exception as error:
        print(f"Error escribiendo metadatos: {error}")
        return False


def download_cover(release_id, destination):
    url = f"{COVERART_URL}/{release_id}/front-500"

    try:
        download_file(url, destination)
        return True
    except Exception:
        return False


def embed_cover(mp3_path, cover_path):
    try:
        audio = ID3(mp3_path)

        with open(cover_path, "rb") as cover:
            image_data = cover.read()

        audio.delall("APIC")

        audio.add(
            APIC(
                encoding=3,
                mime="image/jpeg",
                type=3,
                desc="Cover",
                data=image_data,
            )
        )

        audio.save(v2_version=3)

        return True

    except Exception as error:
        print(f"No se pudo incrustar la portada: {error}")
        return False


def main():

    if len(sys.argv) < 3:
        print("Uso:")
        print("  tag-music.py archivo.mp3 artista")
        sys.exit(1)

    mp3_path = Path(sys.argv[1])
    original_artist = sys.argv[2]

    if not mp3_path.exists():
        print(f"No existe: {mp3_path}")
        sys.exit(1)

    original = read_original_tags(mp3_path)

    title = original["title"] or mp3_path.stem
    artist = original["artist"] or original_artist

    print()
    print("==========================================")
    print("       IDENTIFICANDO LA MÚSICA")
    print("==========================================")
    print()
    print(f"Título : {title}")
    print(f"Artista: {artist}")

    recording = search_musicbrainz(title, artist)

    if recording == "TEMP_ERROR":

        print()
        print("⚠️ MusicBrainz no está disponible ahora.")
        print("Conservamos los metadatos originales.")
        print()

        metadata = original

    elif recording is None:

        print()
        print("No se encontró coincidencia en MusicBrainz.")
        print("Usaremos los metadatos originales.")
        print()

        metadata = original

    else:

        release = get_release(recording)

        metadata = original.copy()

        metadata["title"] = recording.get("title") or original["title"]
        metadata["artist"] = get_artist(recording)

        if release:
            metadata["album"] = release.get("title", "")

            date = release.get("date", "")

            if date:
                metadata["year"] = date[:4]

            release_id = release.get("id")

            if release_id:
                cover_path = mp3_path.with_suffix(".jpg")

                print()
                print("Buscando portada...")

                if download_cover(release_id, cover_path):

                    embed_cover(mp3_path, cover_path)

                    try:
                        cover_path.unlink()
                    except FileNotFoundError:
                        pass

    write_tags(mp3_path, metadata)

    artist_name = clean_name(metadata["artist"])
    title_name = clean_name(metadata["title"])

    album_name = clean_name(metadata.get("album"))

    if not metadata.get("album"):
        album_name = "Singles"

    target_dir = NAVIDROME_DIR / artist_name / album_name

    target_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    target_file = target_dir / f"{title_name}.mp3"

    if target_file.exists():

        print()
        print("⚠️ El archivo ya existe:")
        print(target_file)
        print()
        print("No se sobrescribe.")

        mp3_path.unlink()

        return

    mp3_path.rename(target_file)

    print()
    print("==========================================")
    print("        ✅ ORGANIZACIÓN COMPLETADA")
    print("==========================================")
    print()
    print(f"Artista : {artist_name}")
    print(f"Álbum   : {album_name}")
    print(f"Canción : {title_name}")
    print(f"Año     : {metadata.get('year', '')}")
    print()
    print(f"📁 {target_file}")
    print()


if __name__ == "__main__":
    main()
