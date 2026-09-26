"""The Myntos mark, for the messages that leave the building.

A logo in an email is not the same problem as a logo on a page. Three
constraints decide everything here:

**It cannot be a remote image.** Gmail proxies them, Outlook blocks them until
the reader clicks "download pictures", and a URL fetched when a message is
opened is a read receipt whether or not anyone wanted one. The mark travels
inside the message as a related MIME part instead, so it renders offline, on the
first open, and tells us nothing about who opened it.

**It cannot be the SVG.** The site's mark is an SVG whose two fills are theme
tokens, which is exactly what an email client will not do: Gmail strips SVG
entirely. So this is a PNG, in the light theme's colours, on the white the card
it sits on already is.

**It has to survive being blocked anyway.** Whatever a client does, the word
"Myntos" is next to the mark as text and in the ``alt``, so a blocked image
leaves a brand name rather than a grey box.

The file itself is ``frontend/public/favicon-96.png``, copied rather than
imported: the backend deploys on its own and cannot reach into the frontend's
tree at runtime. Regenerate it with ``pnpm favicons`` in ``frontend/`` and copy
it across; the mark is authored once, in ``frontend/src/components/Logo.astro``.
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources

# The Content-ID the HTML refers to as ``cid:myntos-logo``. A fixed string
# rather than a generated one: it appears in the markup two modules away, and a
# per-message id would have to be threaded through for no gain.
LOGO_CID = "myntos-logo"

# What the mark is drawn at (96px) and shown at (48px). Two-to-one, because a
# retina client renders an email at 2x like anything else and a 48px PNG shown
# at 48px is visibly soft.
LOGO_WIDTH = 48

# The brand green, for the wordmark beside the mark. Written out rather than
# taken from a token: an email has no stylesheet and no theme to resolve one
# against.
INK = "#1c1f1e"
GREEN = "#0f8a68"


@lru_cache(maxsize=1)
def logo_png() -> bytes:
    """The mark, as PNG bytes. Read once per process; it is 4 KB."""
    return (resources.files("myntos.core") / "assets" / "myntos-logo.png").read_bytes()
