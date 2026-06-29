import httpx

from app.core.config import settings

STORE_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
APP_DETAILS_URL = "https://store.steampowered.com/api/appdetails"
OWNED_GAMES_URL = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"

STEAM_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_steam_game",
            "description": (
                "Look up a game's Steam store page - price, description, genres, release date, "
                "Metacritic score. No setup required, works for any game listed on Steam."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The game's title to search for."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_steam_library",
            "description": (
                "Check the user's owned Steam games and playtime. Omit game_name for their most-played "
                "games overall; pass it to check playtime in one specific game. Requires the user to have "
                "set a Steam API key and SteamID64 in Settings, and their game details to be public."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "game_name": {
                        "type": "string",
                        "description": "Optional - a specific owned game to check playtime for.",
                    }
                },
            },
        },
    },
]


async def execute_lookup_steam_game(arguments: dict) -> str:
    query = (arguments.get("query") or "").strip()
    if not query:
        return "No game name given."

    try:
        async with httpx.AsyncClient(timeout=10) as http_client:
            search_response = await http_client.get(STORE_SEARCH_URL, params={"term": query, "cc": "us", "l": "en"})
            search_response.raise_for_status()
            items = search_response.json().get("items") or []
            if not items:
                return f"No Steam store results found for '{query}'."

            appid = items[0]["id"]
            details_response = await http_client.get(APP_DETAILS_URL, params={"appids": appid, "l": "en"})
            details_response.raise_for_status()
            data = details_response.json().get(str(appid), {})
    except httpx.HTTPError as exc:
        return f"Steam store lookup failed: {exc}"

    if not data.get("success"):
        return f"No Steam store details found for '{query}'."

    app = data["data"]
    parts = [app.get("name", query)]

    if app.get("short_description"):
        parts.append(app["short_description"])

    genres = [g["description"] for g in app.get("genres", [])]
    if genres:
        parts.append(f"Genres: {', '.join(genres)}")

    release_date = (app.get("release_date") or {}).get("date")
    if release_date:
        parts.append(f"Released: {release_date}")

    price_overview = app.get("price_overview")
    if price_overview:
        parts.append(f"Price: {price_overview.get('final_formatted')}")
    elif app.get("is_free"):
        parts.append("Price: Free to Play")

    metacritic = app.get("metacritic")
    if metacritic:
        parts.append(f"Metacritic: {metacritic.get('score')}/100")

    return "\n".join(parts)


async def execute_fetch_steam_library(arguments: dict) -> str:
    if not settings.steam_api_key or not settings.steam_id:
        return "Steam library isn't configured - set a Steam API key and SteamID64 in Settings."

    try:
        async with httpx.AsyncClient(timeout=10) as http_client:
            response = await http_client.get(
                OWNED_GAMES_URL,
                params={
                    "key": settings.steam_api_key,
                    "steamid": settings.steam_id,
                    "format": "json",
                    "include_appinfo": 1,
                    "include_played_free_games": 1,
                },
            )
            response.raise_for_status()
            games = (response.json().get("response") or {}).get("games") or []
    except httpx.HTTPError as exc:
        return f"Steam library lookup failed: {exc}"

    if not games:
        return "No games found - the Steam profile/game details may be private, or the SteamID/API key is wrong."

    game_name = (arguments.get("game_name") or "").strip().lower()
    if game_name:
        matches = [g for g in games if game_name in g.get("name", "").lower()]
        if not matches:
            return f"No owned game matching '{game_name}' found."
        return "\n".join(
            f"{g['name']}: {round(g.get('playtime_forever', 0) / 60, 1)} hours played" for g in matches[:5]
        )

    top_games = sorted(games, key=lambda g: g.get("playtime_forever", 0), reverse=True)[:10]
    lines = [f"{len(games)} games owned. Most played:"]
    lines.extend(f"- {g['name']}: {round(g.get('playtime_forever', 0) / 60, 1)}h" for g in top_games)
    return "\n".join(lines)
