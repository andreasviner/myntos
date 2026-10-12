"""A coarse name for the device behind a session: "Firefox på Linux".

The session list exists so a person can recognise their own logins and end the
ones they do not. For that, the browser and the operating system are enough,
and they are all this module will say. The raw ``User-Agent`` (stored on
:class:`~myntos.domain.identity.models.UserSession` since the first login) is
never sent back: it is a fingerprint as much as a description, and a version
number like ``Chrome/131.0.6778.86`` helps nobody tell their phone from their
laptop.

The IP address stored beside it is not shown either. It is kept for the
incident question ("from where was I signed in"), which is asked of us, not of
the screen; showing it would invite a lookup of where that address is, and the
only way to answer that is to send it to a geolocation service.

Parsing is deliberately crude: a handful of substring checks, in the order that
matters (every Chromium browser also says "Chrome", and every browser on iOS
also says "Safari"). A wrong guess costs a label; a library that tracks every
user agent on the market costs a dependency for a cosmetic answer.
"""

# Ordered: the first match wins, so the browsers that also claim to be Chrome or
# Safari come before them.
_BROWSERS: tuple[tuple[str, str], ...] = (
    ("Edg/", "Edge"),
    ("EdgiOS/", "Edge"),
    ("OPR/", "Opera"),
    ("SamsungBrowser/", "Samsung Internet"),
    ("Vivaldi/", "Vivaldi"),
    ("Firefox/", "Firefox"),
    ("FxiOS/", "Firefox"),
    ("CriOS/", "Chrome"),
    ("Chrome/", "Chrome"),
    ("Safari/", "Safari"),
)

# Ordered for the same reason: an iPhone says "like Mac OS X", and Android says
# "Linux".
_SYSTEMS: tuple[tuple[str, str], ...] = (
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Android", "Android"),
    ("CrOS", "ChromeOS"),
    ("Windows", "Windows"),
    ("Mac OS X", "macOS"),
    ("Macintosh", "macOS"),
    ("Linux", "Linux"),
)

# Clients that are not browsers. A bearer-token script is a legitimate session
# and should be recognisable as one rather than as "unknown".
_PROGRAMS: tuple[str, ...] = ("curl/", "python-httpx/", "python-requests/", "testclient")


def describe_device(user_agent: str | None) -> str | None:
    """A short, human description of ``user_agent``, or ``None`` when unknown."""
    if not user_agent:
        return None
    lowered = user_agent.lower()
    if any(program.lower() in lowered for program in _PROGRAMS):
        return "Skript eller program"
    browser = next((name for marker, name in _BROWSERS if marker in user_agent), None)
    system = next((name for marker, name in _SYSTEMS if marker in user_agent), None)
    if browser and system:
        return f"{browser} på {system}"
    return browser or system
