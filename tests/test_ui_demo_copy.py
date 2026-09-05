"""Role 3 drift guards for the spoken demo narrative.

`PROGRESS.md:90` carries an open P0 decision: whether Gardens by the Bay is a soft
preference in the spoken demo, with wording kept consistent. Both halves of that are
true today and nothing enforces either, so these tests pin them from both sides — the
copy the judges read and the constraints the parser actually produces.
"""

from __future__ import annotations

import re
from pathlib import Path

BROWSER_CLIENT = Path(__file__).resolve().parents[1] / "public" / "index.html"


def browser_prompt() -> str:
    """The browser form must not inject a synthetic journey into a live session."""
    html = BROWSER_CLIENT.read_text(encoding="utf-8")
    match = re.search(r'<textarea id="prompt"[^>]*>(.*?)</textarea>', html, re.DOTALL)
    assert match is not None, "the browser client must contain the journey prompt field"
    return match.group(1).strip()


def test_browser_requires_caregiver_prompt_in_live_mode() -> None:
    assert browser_prompt() == ""


def test_browser_has_no_simulated_live_events() -> None:
    html = BROWSER_CLIENT.read_text(encoding="utf-8")
    assert "Simulate heavy rain" not in html
    assert "Mum is more tired" not in html
