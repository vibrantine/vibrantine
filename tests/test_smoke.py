"""Smoke test ensuring the package imports cleanly."""

import vibrantine


def test_package_imports() -> None:
    assert vibrantine is not None
