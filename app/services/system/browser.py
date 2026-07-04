import os
import sys
import webbrowser


def open_url(url: str) -> None:
    """Best-effort local launch of a URL/deep-link in the user's real default browser or
    registered protocol handler (e.g. spotify:). os.startfile handles custom protocol handlers
    registered with Windows, which webbrowser.open cannot invoke reliably there; everywhere else
    falls back to the standard library's browser opener."""
    if sys.platform == "win32":
        try:
            os.startfile(url)
            return
        except OSError:
            pass
    webbrowser.open(url)
