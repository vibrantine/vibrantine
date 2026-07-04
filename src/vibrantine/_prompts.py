"""Prompt files are package resources so long prompts can leave Python modules."""

from importlib.resources import files


def load_prompt(package: str, filename: str = "system.md") -> str:
    """Load a markdown prompt from a package's `prompts/` directory."""

    return files(package).joinpath("prompts", filename).read_text(encoding="utf-8").strip()
