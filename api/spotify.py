import os
import random
from base64 import b64encode

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


def make_svg(data, token):
    playing = data.get("item") if isinstance(data, dict) else None
    active = bool(playing and data.get("is_playing", False))
    if not playing:
        recent = spotify_request(RECENT_URL, token)
        tracks = [x.get("track") for x in recent.get("items", []) if x.get("track")]
        playing = tracks[0] if tracks else None

    count = 84 if active else 0
    bars = "".join("<div class='bar'></div>" for _ in range(count))
    css = "".join(
        f".bar:nth-child({i}) {{ left: {1 + (i - 1) * 4}px; animation-duration: {random.randint(1000, 1350)}ms; }}"
        for i in range(1, count + 1)
    )
    if playing:
        images = playing.get("album", {}).get("images", [])
        image = ""
        if images:
            result = requests.get(images[min(1, len(images)-1)]["url"], timeout=10)
            result.raise_for_status()
            image = b64encode(result.content).decode()
        artist = ", ".join(a.get("name", "") for a in playing.get("artists", []))
        song = playing.get("name", "Unknown track")
    else:
        image, artist, song = "", "", "Nothing played recently"

    return render_template(
        "spotify.html.j2",
        contentBar=bars,
        barCSS=css,
        artistName=str(escape(artist)),
        songName=str(escape(song)),
        image=image,
        status="Listening to:" if active else "Was playing:",
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
