import os
from base64 import b64encode
from datetime import datetime, timezone
from unicodedata import combining, east_asian_width

import requests
from flask import Flask, Response, redirect, render_template, request
from markupsafe import escape

app = Flask(__name__)
TOKEN_URL = "https://accounts.spotify.com/api/token"
CURRENT_URL = "https://api.spotify.com/v1/me/player/currently-playing"
RECENT_URL = "https://api.spotify.com/v1/me/player/recently-played?limit=10"


def spotify_request(url, token):
    response = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    if response.status_code == 204:
        return {}
    response.raise_for_status()
    return response.json()


def refresh_token():
    client_id = os.environ["SPOTIFY_CLIENT_ID"]
    secret = os.environ["SPOTIFY_SECRET_ID"]
    refresh = os.environ["SPOTIFY_REFRESH_TOKEN"]
    auth = b64encode(f"{client_id}:{secret}".encode()).decode()
    response = requests.post(
        TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": refresh},
        headers={"Authorization": f"Basic {auth}"},
        timeout=10,
    )
    if not response.ok:
        app.logger.error("Spotify token refresh failed: HTTP %s", response.status_code)
    response.raise_for_status()
    return response.json()["access_token"]


def truncate_label(value, max_units):
    """Fit text to the SVG's available width, including wide Unicode glyphs."""
    value = " ".join(str(value or "").split())
    units = 0
    result = []
    for char in value:
        width = 0 if combining(char) else (2 if east_asian_width(char) in ("F", "W") else 1)
        if units + width > max_units:
            return "".join(result).rstrip() + "…"
        result.append(char)
        units += width
    return value


def make_svg(data, token):
    track = data.get("item") if isinstance(data, dict) else None
    active = bool(track and data.get("is_playing", False))
    played_at = None
    if not track:
        recent = spotify_request(RECENT_URL, token)
        entry = next((x for x in recent.get("items", []) if x.get("track")), None)
        track = entry.get("track") if entry else None
        played_at = entry.get("played_at") if entry else None

    if active:
        status = "NOW PLAYING"
    elif played_at:
        try:
            dt = datetime.fromisoformat(played_at.replace("Z", "+00:00"))
            status = "LAST PLAYED · " + dt.astimezone(timezone.utc).strftime("%d %b, %H:%M UTC")
        except ValueError:
            status = "LAST PLAYED"
    else:
        status = "LAST PLAYED" if track else "NOTHING PLAYED YET"

    image = ""
    if track:
        images = track.get("album", {}).get("images", [])
        if images:
            try:
                result = requests.get(images[min(1, len(images) - 1)]["url"], timeout=6)
                result.raise_for_status()
                if result.headers.get("Content-Type", "").startswith("image/") and len(result.content) < 2_000_000:
                    image = b64encode(result.content).decode()
            except requests.RequestException:
                app.logger.warning("Spotify artwork unavailable; using placeholder")
        song = truncate_label(track.get("name", "Unknown track"), 29)
        artist = truncate_label(", ".join(a.get("name", "") for a in track.get("artists", [])), 42)
        album = track.get("album", {})
        album_name = album.get("name", "")
        year = (album.get("release_date") or "")[:4]
        metadata = " · ".join(part for part in (album_name, year) if part)
        metadata = truncate_label(metadata, 45)
    else:
        song, artist, metadata = "Nothing played recently", "", ""

    return render_template(
        "spotify.html.j2",
        artistName=artist,
        songName=song,
        albumMeta=metadata,
        image=image,
        status=status,
        active=active,
    )


@app.route("/track")
def track_redirect():
    try:
        token = refresh_token()
        data = spotify_request(CURRENT_URL, token)
        track = data.get("item") if isinstance(data, dict) else None
        if not track or track.get("type") != "track":
            recent = spotify_request(RECENT_URL, token)
            track = next(
                (entry.get("track") for entry in recent.get("items", [])
                 if entry.get("track", {}).get("type") == "track"),
                None,
            )
        url = (track or {}).get("external_urls", {}).get("spotify")
        if not url or not url.startswith("https://open.spotify.com/track/"):
            return redirect("https://open.spotify.com/user/amohaimen", code=302)
        response = redirect(url, code=302)
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Vercel-CDN-Cache-Control"] = "no-store"
        return response
    except (requests.RequestException, KeyError, ValueError, IndexError):
        app.logger.exception("Spotify track redirect failed")
        return redirect("https://open.spotify.com/user/amohaimen", code=302)


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def catch_all(path):
    if request.args.get("redirect") == "track":
        return track_redirect()
    try:
        token = refresh_token()
        data = spotify_request(CURRENT_URL, token)
        svg = make_svg(data, token)
        response = Response(svg, mimetype="image/svg+xml")
        response.headers["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
        response.headers["CDN-Cache-Control"] = "no-store"
        response.headers["Vercel-CDN-Cache-Control"] = "no-store"
        return response
    except (requests.RequestException, KeyError, ValueError, IndexError) as exc:
        app.logger.exception("Spotify now-playing request failed")
        return Response("Spotify integration temporarily unavailable", status=502, mimetype="text/plain")


if __name__ == "__main__":
    app.run()
