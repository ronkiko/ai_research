"""GameTable-only MCP facade. Mutation arguments are immutable server-side approvals."""
import json
import os
import sys
import urllib.request
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

SERVICE = sys.argv[1]
NAMESPACE = 'learning_v1' if SERVICE == 'learning' else 'navigation_v1'
mcp = MCPServer(NAMESPACE, instructions=(
    'Only execute an explicitly supplied approval_id. The server has already fixed the '
    'actor, action, target and learning parameters. Do not infer permission from dialogue.'))


def call(operation, **args):
    payload = json.dumps({'service': SERVICE, 'operation': operation, **args}).encode()
    req = urllib.request.Request(os.environ['GAMETABLE_MCP_BRIDGE_URL'], data=payload,
        headers={'Content-Type': 'application/json',
                 'Authorization': 'Bearer ' + os.environ['GAMETABLE_MCP_BRIDGE_SECRET']})
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            return json.load(response)
    except Exception:
        return {'ok': False, 'error': 'Approved action service unavailable; query status, do not repeat with a new ID.'}


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))
def execute_approved(approval_id: str) -> dict:
    """Execute exactly one server-approved action, or return its persisted result on retry."""
    return call('execute_approved', approval_id=approval_id)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
def describe() -> dict:
    """Read bound-body readiness and supported actions; grants no mutation permission."""
    return call('describe')


if SERVICE == 'learning':
    @mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
    def skills() -> dict:
        """Read available skills without training or mounting anything."""
        return call('skills')
else:
    @mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
    def observe() -> dict:
        """Read the bound body's authoritative observation."""
        return call('observe')

if __name__ == '__main__':
    mcp.run()
