"""
`is_underchunked` decides which files are worth spending an LLM chunking pass on,
and its reasons are what a run report calls "degraded". A reason that fires on
every file of a language is worse than no reason at all: it buys an LLM call per
file and it buries the genuine timeout and exception degradations in noise.

The JS/TS template-literal rule used to be `count > 3`, which flagged 59 of
farmworth_frontend's 1,271 files — `/api/draw/farm/${d.id}` is not embedded
structure, it is how JavaScript writes a URL. It is now gated on literal length,
matching the embedded_html / long_docstring patterns it sits beside, so it
catches only what it was always meant to catch: a markup or SQL blob parked
inside a template literal where the structural chunker cannot see it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from llm_chunker import is_underchunked


def _chunks(n):
    """Enough chunks at a low enough density to keep rules 1 and 2 quiet."""
    return [{"content": "x"} for _ in range(n)]


ORDINARY_JS = """
export function boundsToBbox(mapBounds) {
	let sw = `${mapBounds?._southWest?.lng},${mapBounds?._southWest?.lat}`;
	let ne = `${mapBounds?._northEast?.lng},${mapBounds?._northEast?.lat}`;
	let bbox = `${sw},${ne}`;
	return bbox;
}

export async function loadFarm(d, name) {
	const res = await fetch(`/api/draw/farm/${d.id}`);
	if (!res.ok) {
		throw new Error(`Invalid farm response: ${res.status}`);
	}
	notify(`Created farm "${name}"`, 'success');
	window.open(`${origin}/farm/${d.id}`, '_blank');
	return res;
}
"""

MARKUP_IN_A_LITERAL = """
export function popupFor(parcel) {
	return `
		<div class="popup">
			<h3 class="popup__title">${parcel.name}</h3>
			<table class="popup__table">
				<tr><td>Acres</td><td>${parcel.acres}</td></tr>
				<tr><td>Owner</td><td>${parcel.owner}</td></tr>
				<tr><td>APN</td><td>${parcel.apn}</td></tr>
			</table>
			<a class="popup__link" href="/parcel/${parcel.id}">Open parcel</a>
		</div>
	`;
}
"""


class TestTemplateLiteralRule:
    def test_ordinary_interpolation_is_not_a_reason(self):
        """Eight interpolations, none of them structure. The old rule fired at four."""
        _, reason = is_underchunked("src/lib/utils/map_helpers.js", ORDINARY_JS, _chunks(4), "javascript")
        assert "template_literals" not in reason

    def test_ordinary_interpolation_alone_needs_no_enrichment(self):
        needs, reason = is_underchunked("src/lib/utils/map_helpers.js", ORDINARY_JS, _chunks(4), "javascript")
        assert needs is False, reason
        assert reason == "adequately_chunked"

    def test_a_markup_blob_inside_a_literal_still_fires(self):
        needs, reason = is_underchunked("src/lib/map/popups.js", MARKUP_IN_A_LITERAL, _chunks(2), "javascript")
        assert needs is True
        assert "template_literals" in reason

    def test_the_reason_counts_only_substantial_literals(self):
        """One blob among many ordinary interpolations reports one, not nine."""
        _, reason = is_underchunked(
            "src/lib/map/popups.js", ORDINARY_JS + MARKUP_IN_A_LITERAL, _chunks(4), "javascript"
        )
        assert "template_literals (1 substantial instances)" in reason

    def test_the_rule_is_javascript_only(self):
        _, reason = is_underchunked("app/views.py", MARKUP_IN_A_LITERAL, _chunks(4), "python")
        assert "template_literals" not in reason
