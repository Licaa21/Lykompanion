from urllib.parse import quote, urljoin

import httpx

from app.core.config import settings
from app.services.llm.client import get_client

WEB_SEARCH_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for current information - patch notes, release dates, build "
                "guides, wiki details, or anything else you're unsure about or that may have "
                "changed since your training. Use it instead of guessing when you're not confident."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query."},
                },
                "required": ["query"],
            },
        },
    },
]


SHOW_IMAGE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "show_image",
            "description": (
                "Find a real picture of something on the web and display it inline in the chat. "
                "Use this WHENEVER the user asks to see, show, or pull up a picture/image/photo of "
                "a thing (a boss, item, location, character, map, real-world object, etc.), or "
                "whenever a picture would clearly help your answer. This is the ONLY way to show a "
                "web picture - do NOT use take_screenshot for this (that captures the user's own "
                "screen, not the web). The tool returns a ready-to-use markdown image line: paste "
                "it into your reply exactly as given."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to find a picture of, e.g. 'Malenia Elden Ring boss'.",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


async def _image_candidate_loads(http_client: httpx.AsyncClient, absolute_url: str) -> bool:
    """Confirm a candidate image URL actually returns image bytes server-side, so we never hand
    the model a dead/hotlink-protected/auth-gated URL that renders as '[image unavailable]'."""
    try:
        async with http_client.stream(
            "GET", absolute_url, headers=SEARXNG_HEADERS, follow_redirects=True, timeout=8
        ) as response:
            if response.status_code != 200:
                return False
            return response.headers.get("content-type", "").startswith("image/")
    except Exception:
        return False


async def _search_searxng_images(http_client: httpx.AsyncClient, query: str) -> tuple[str, str] | None:
    """Return (proxied_image_url, title) for the first candidate that actually loads, or None.
    Walks several results and validates each rather than trusting the first blindly."""
    try:
        response = await http_client.get("/search", params={"q": query, "format": "json", "categories": "images"})
        response.raise_for_status()
        results = response.json().get("results", [])
    except Exception:
        return None
    for result in results[:8]:
        image_url = result.get("img_src")
        if not image_url:
            continue
        # Resolve relative SearXNG proxy paths to absolute before proxying through FastAPI.
        absolute = urljoin(settings.searxng_base_url, image_url)
        if not await _image_candidate_loads(http_client, absolute):
            continue
        title = (result.get("title") or query).strip()
        return f"/api/proxy/image?url={quote(absolute, safe='')}", title
    return None


async def execute_image_search(query: str) -> str | None:
    """Image-only SearXNG lookup — returns a single proxied image URL or None. No LLM call, no
    result text; used for cheap auto-injection where the model already knows the answer. Uses
    SearXNG regardless of web_search_provider — it's the only image-capable path, so when text
    search runs through OpenRouter's web plugin (no images) this still lets pictures work."""
    async with httpx.AsyncClient(
        base_url=settings.searxng_base_url, timeout=10, headers=SEARXNG_HEADERS
    ) as http_client:
        found = await _search_searxng_images(http_client, query)
        return found[0] if found else None


async def execute_show_image(arguments: dict) -> str:
    """Agent tool: find a validated web image and return a ready-to-embed markdown line."""
    query = (arguments.get("query") or "").strip()
    if not query:
        return "No image query given."
    # Always use SearXNG for images regardless of web_search_provider: OpenRouter's web plugin
    # returns text only, so this is the fallback that lets pictures work even when text search
    # runs through OpenRouter. If SearXNG is unreachable, _search_searxng_images returns None
    # below and we report "no picture found" (same graceful degrade as an empty result set).
    async with httpx.AsyncClient(
        base_url=settings.searxng_base_url, timeout=12, headers=SEARXNG_HEADERS
    ) as http_client:
        found = await _search_searxng_images(http_client, query)
    if not found:
        return (
            f"No usable picture found for '{query}'. Tell the user you couldn't find a good "
            "image - do not invent or embed a URL."
        )
    url, title = found
    return f"Embed this image in your reply exactly as written, on its own line:\n![{title}]({url})"


SEARXNG_HEADERS = {
    # Some SearXNG instances run a bot/limiter filter that rejects requests without
    # browser-like headers, independent of whether json is in the enabled formats list.
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


async def _execute_web_search_searxng(query: str) -> str:
    async with httpx.AsyncClient(
        base_url=settings.searxng_base_url, timeout=15, headers=SEARXNG_HEADERS
    ) as http_client:
        try:
            response = await http_client.get("/search", params={"q": query, "format": "json", "categories": "general"})
            response.raise_for_status()
        except Exception as exc:
            return f"Web search failed: {exc}"

        results = response.json().get("results", [])[:5]
        if results:
            return "\n\n".join(
                f"{r.get('title', '')}\n{r.get('url', '')}\n{r.get('content', '')}" for r in results
            )

        # No general-category hits. Over-restrictive queries (exact-phrase quotes, long AND/OR
        # chains) are the usual cause and make the model reword and re-search in a loop — tell it
        # so it broadens instead. Deliberately no image lookup here: web_search is a text tool
        # called in the hot agent loop, and validating image candidates (streaming up to 8 URLs)
        # added seconds per call for no text value. The model has show_image for pictures.
        return (
            f"No results for '{query}'. If the query used quoted phrases or many required terms, "
            "retry ONCE with a shorter, unquoted version; otherwise tell the user you couldn't find it."
        )


async def execute_web_search(arguments: dict) -> str:
    query = (arguments.get("query") or "").strip()
    if not query:
        return "No search query given."

    if settings.web_search_provider == "searxng":
        return await _execute_web_search_searxng(query)

    try:
        response = await get_client("openrouter").chat.completions.create(
            model=settings.openrouter_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Search the web for the user's query and answer concisely with the key facts, citing "
                        "source URLs. If the search results include a direct image URL (a link ending in an "
                        "image extension like .jpg/.jpeg/.png/.webp, not a webpage) that's genuinely relevant "
                        "to the query, include it explicitly on its own line formatted as 'Image: <url>'. Only "
                        "do this for image URLs actually present in the results — never invent or guess one."
                    ),
                },
                {"role": "user", "content": query},
            ],
            extra_body={"plugins": [{"id": "web", "max_results": 5}]},
        )
    except Exception as exc:
        return f"Web search failed: {exc}"

    return response.choices[0].message.content or f"No web search results found for '{query}'."
