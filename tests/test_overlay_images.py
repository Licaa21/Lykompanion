from app.api.chat import _extract_overlay_images


def test_extracts_direct_https_image():
    reply = "Here is the map: ![BG3 owlbear cave](https://example.com/map.png) — good luck!"
    assert _extract_overlay_images(reply) == [("https://example.com/map.png", "BG3 owlbear cave")]


def test_unwraps_proxy_image_url_to_real_source():
    reply = "![owlbear](/api/proxy/image?url=https%3A%2F%2Fcdn.site%2Fpic.jpg&w=200)"
    assert _extract_overlay_images(reply) == [("https://cdn.site/pic.jpg", "owlbear")]


def test_ignores_non_http_and_plain_links():
    reply = "See [the wiki](https://wiki.example.com) and ![local](/static/x.png)."
    assert _extract_overlay_images(reply) == []


def test_multiple_images_in_order():
    reply = "![a](https://x/1.png) text ![b](https://x/2.png)"
    assert _extract_overlay_images(reply) == [
        ("https://x/1.png", "a"),
        ("https://x/2.png", "b"),
    ]
