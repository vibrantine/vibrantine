"""Prove the built wheel works without the source checkout on sys.path."""

from pathlib import Path
from tempfile import TemporaryDirectory

import vibrantine
from vibrantine import run_commission_sync
from vibrantine.examples.recursive_research import RecursiveResearchCommission
from vibrantine.tools import ReadTool
from vibrantine.tools.read import ReadInput


def main() -> None:
    assert vibrantine.__all__
    assert RecursiveResearchCommission.system_prompt

    with TemporaryDirectory() as directory:
        path = Path(directory) / "proof.txt"
        path.write_bytes(b"installed wheel\n")
        result = run_commission_sync(ReadTool(), ReadInput(path=path))

    assert result.status == "success"
    assert result.output is not None
    assert result.output.content == "installed wheel\n"
    print("Installed package smoke test passed.")


if __name__ == "__main__":
    main()
