"""License firewall classification — the permitted-behavior tiers."""

from __future__ import annotations

import pytest

from alchemia.github.licensing import classify_license, license_decision


@pytest.mark.parametrize(
    ("spdx", "expected"),
    [
        ("MIT", "permissive"),
        ("Apache-2.0", "permissive"),
        ("BSD-3-Clause", "permissive"),
        ("ISC", "permissive"),
        ("GPL-3.0", "copyleft"),
        ("AGPL-3.0", "copyleft"),
        ("MPL-2.0", "copyleft"),
        ("LGPL-2.1", "copyleft"),
        ("", "unknown"),
        ("NOASSERTION", "none"),
        ("SOME-WEIRD-LICENSE", "unknown"),
    ],
)
def test_classify_license(spdx, expected):
    assert classify_license(spdx) == expected


def test_license_decision_firewall():
    assert license_decision("permissive") == "code-adaptation-with-attribution"
    assert license_decision("copyleft").startswith("idea-or-interface-only")
    assert license_decision("none") == "no-code-or-asset-copying"
    assert license_decision("unknown") == "no-code-or-asset-copying"
