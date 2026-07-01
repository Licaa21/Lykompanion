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


async def _search_searxng_images(http_client: httpx.AsyncClient, query: str) -> str | None:
    try:
        response = await http_client.get("/search", params={"q": query, "format": "json", "categories": "images"})
        response.raise_for_status()
        results = response.json().get("results", [])
    except Exception:
        return None
    for result in results:
        image_url = result.get("img_src")
        if not image_url:
            continue
        # Resolve relative SearXNG proxy paths to absolute before proxying through FastAPI.
        absolute = urljoin(settings.searxng_base_url, image_url)
        return f"/api/proxy/image?url={quote(absolute, safe='')}"
    return None


async def execute_image_search(query: str) -> str | None:
    """Image-only SearXNG lookup — returns a single image URL or None. No LLM call, no
    result text; used for cheap auto-injection where the model already knows the answer."""
    if settings.web_search_provider != "searxng":
        return None
    async with httpx.AsyncClient(
        base_url=settings.searxng_base_url, timeout=10, headers=SEARXNG_HEADERS
    ) as http_client:
        return await _search_searxng_images(http_client, query)


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
        if not results:
            text = f"No web search results found for '{query}'."
        else:
            text = "\n\n".join(
                f"{r.get('title', '')}\n{r.get('url', '')}\n{r.get('content', '')}" for r in results
            )

        image_url = await _search_searxng_images(http_client, query)
        if image_url:
            text += f"\n\nImage: {image_url}"

        return text


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
