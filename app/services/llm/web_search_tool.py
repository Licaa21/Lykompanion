from app.core.config import settings
from app.services.llm.client import client

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


async def execute_web_search(arguments: dict) -> str:
    query = (arguments.get("query") or "").strip()
    if not query:
        return "No search query given."

    try:
        response = await client.chat.completions.create(
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
