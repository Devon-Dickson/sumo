"""HTTP surface: a Newznab indexer and a SABnzbd download client on one port.

Sonarr is pointed at the same host twice — once as a Newznab indexer (base URL
``/``) and once as a SABnzbd download client (URL base ``sabnzbd``).
"""

from __future__ import annotations

import contextlib
import logging

from fastapi import APIRouter, FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse

from . import newznab, sabnzbd
from .config import Config
from .downloader import DownloadManager
from .nhk import NhkClient
from .releases import build_nzb, episode_id_from_any, parse_nzb, release_name

log = logging.getLogger(__name__)

XML_MEDIA_TYPE = "application/xml"


async def _uploaded_nzb(form) -> bytes:
    """Pull the posted NZB out of a multipart form.

    Sonarr uploads it under the field name ``name``; SABnzbd's own docs and
    some other clients use ``nzbfile``. Rather than guess, take the first part
    that is actually a file, and fall back to a string field holding raw XML.
    """
    if not form:
        return b""

    for value in form.values():
        if hasattr(value, "read"):
            return await value.read()

    for key in ("nzbfile", "name"):
        value = form.get(key)
        # Guard against grabbing a genuine string parameter -- `name` doubles
        # as the command word for queue/history deletes.
        if isinstance(value, str) and value.lstrip().startswith("<"):
            return value.encode()
    return b""


def _unauthorised() -> Response:
    return Response(
        content=newznab.error_xml(100, "Incorrect user credentials"),
        media_type=XML_MEDIA_TYPE,
        status_code=401,
    )


def create_app(config: Config | None = None) -> FastAPI:
    config = config or Config.from_env()
    client = NhkClient(
        lang=config.lang,
        cache_ttl=config.cache_ttl,
        season_offsets=config.season_offsets,
    )
    manager = DownloadManager(config, client)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        await manager.start()
        log.info(
            "sumo-bridge ready on %s (category=%s, complete_dir=%s)",
            config.public_url,
            config.category,
            config.category_dir,
        )
        if config.api_key_was_generated:
            log.warning(
                "SUMO_API_KEY was not set; generated one for this run: %s "
                "(set it in the environment to keep it stable across restarts)",
                config.api_key,
            )
        try:
            yield
        finally:
            await manager.stop()

    app = FastAPI(title="NHK Grand Sumo Bridge", version="1.0", lifespan=lifespan)
    app.state.config = config
    app.state.client = client
    app.state.manager = manager

    def authorised(key: str | None) -> bool:
        return key == config.api_key

    # -- Newznab indexer --------------------------------------------------

    indexer = APIRouter()

    @indexer.get("/api")
    async def newznab_api(  # noqa: PLR0911 - a protocol dispatch, one return per mode
        t: str = Query("caps"),
        apikey: str | None = Query(None),
        q: str | None = Query(None),
        season: str | None = Query(None),
        ep: str | None = Query(None),
        tvdbid: str | None = Query(None),
        limit: int = Query(100),
        offset: int = Query(0),
    ) -> Response:
        if t == "caps":
            # Sonarr fetches caps before it has a key configured.
            return Response(newznab.caps_xml(), media_type=XML_MEDIA_TYPE)

        if not authorised(apikey):
            return _unauthorised()

        if t not in ("search", "tvsearch", "tv-search"):
            return Response(
                newznab.error_xml(202, f"No such function '{t}'"),
                media_type=XML_MEDIA_TYPE,
                status_code=400,
            )

        episodes = await client.episodes()

        if tvdbid and str(tvdbid) != str(newznab.TVDB_SERIES_ID):
            episodes = []
        if season:
            episodes = [e for e in episodes if str(e.season) == str(season).lstrip("0")]
        if ep:
            episodes = [e for e in episodes if str(e.episode) == str(ep).lstrip("0")]
        if q:
            needle = q.lower().replace(".", " ").strip()
            if needle and "sumo" not in needle:
                episodes = []

        total = len(episodes)
        window = episodes[offset : offset + max(1, limit)]
        return Response(
            newznab.feed_xml(window, config, total=total), media_type=XML_MEDIA_TYPE
        )

    @indexer.get("/download/{nhk_id}.nzb")
    async def download_nzb(nhk_id: str, apikey: str | None = Query(None)) -> Response:
        if not authorised(apikey):
            return _unauthorised()
        episode = await client.get(nhk_id)
        if episode is None:
            return Response("unknown episode", status_code=404)
        name = release_name(episode, config.release_group)
        return Response(
            build_nzb(episode, name),
            media_type="application/x-nzb",
            headers={"Content-Disposition": f'attachment; filename="{name}.nzb"'},
        )

    # -- SABnzbd download client ------------------------------------------

    sab = APIRouter(prefix="/sabnzbd")

    async def _add(episode_id: str | None, name_hint: str | None) -> JSONResponse:
        if episode_id is None:
            return JSONResponse(
                {"status": False, "error": "no NHK episode id in submitted NZB"},
                status_code=400,
            )
        episode = await client.get(episode_id)
        if episode is None:
            return JSONResponse(
                {"status": False, "error": f"episode {episode_id} not available"},
                status_code=404,
            )
        name = name_hint or release_name(episode, config.release_group)
        job = await manager.add(episode, name)
        return JSONResponse({"status": True, "nzo_ids": [job.nzo_id]})

    @sab.api_route("/api", methods=["GET", "POST"])
    async def sabnzbd_api(request: Request) -> Response:  # noqa: PLR0911
        params = dict(request.query_params)
        form = {}
        if request.method == "POST":
            form_data = await request.form()
            params.update(
                {k: v for k, v in form_data.items() if isinstance(v, str)}
            )
            form = form_data

        if not authorised(params.get("apikey")):
            return JSONResponse(
                {"status": False, "error": "API Key Incorrect"}, status_code=401
            )

        mode = params.get("mode", "")

        if mode == "version":
            return JSONResponse(sabnzbd.version_response())

        if mode == "get_config":
            return JSONResponse(sabnzbd.config_response(config))

        if mode == "addfile":
            return await _add(parse_nzb(await _uploaded_nzb(form)), params.get("nzbname"))

        if mode == "addurl":
            return await _add(
                episode_id_from_any(params.get("name")), params.get("nzbname")
            )

        if mode == "queue":
            if params.get("name") == "delete":
                removed = manager.remove(
                    params.get("value", ""), params.get("del_files") == "1"
                )
                return JSONResponse({"status": removed})
            return JSONResponse(sabnzbd.queue_response(manager))

        if mode == "history":
            if params.get("name") == "delete":
                removed = manager.remove(
                    params.get("value", ""), params.get("del_files") == "1"
                )
                return JSONResponse({"status": removed})
            return JSONResponse(sabnzbd.history_response(manager))

        return JSONResponse(
            {"status": False, "error": f"unsupported mode '{mode}'"}, status_code=400
        )

    # -- diagnostics ------------------------------------------------------

    @app.get("/health")
    async def health() -> dict:
        episodes = await client.episodes()
        return {
            "status": "ok",
            "episodes": len(episodes),
            "queued": len(manager.queue),
            "history": len(manager.history),
            "latest": (
                {
                    "release": release_name(episodes[0], config.release_group),
                    "season": episodes[0].season,
                    "episode": episodes[0].episode,
                }
                if episodes
                else None
            ),
        }

    app.include_router(indexer)
    app.include_router(sab)
    return app
