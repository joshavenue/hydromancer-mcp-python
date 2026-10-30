"""Support both MCP Python SDK majors: 2.x (MCPServer) and 1.x (FastMCP). Their tool, prompt and
resource APIs used here are the same."""

try:
    from mcp.server.mcpserver import MCPServer as Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as Server  # type: ignore[no-redef]
    from mcp.server.fastmcp.exceptions import ToolError  # type: ignore[no-redef]

__all__ = ["Server", "ToolError"]
