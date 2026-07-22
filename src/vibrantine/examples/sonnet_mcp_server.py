"""Run the first explicit Commission composition root as a local stdio MCP server."""

from typing import Any

from pydantic import BaseModel

from vibrantine import CancelToken, Commission, CommissionResult, run_commission
from vibrantine.examples.compose_sonnet import ComposeSonnetCommission
from vibrantine.mcp.server import create_commission_mcp_server


async def _invoke(
    commission: Commission[Any, Any],
    input: BaseModel,
    *,
    cancel: CancelToken,
) -> CommissionResult[Any]:
    return await run_commission(commission, input, cancel=cancel)


server = create_commission_mcp_server(
    commissions=(ComposeSonnetCommission(),),
    invoke=_invoke,
)


def main() -> None:
    """Start the application-owned composition root over local stdio."""
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
