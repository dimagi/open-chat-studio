from pydantic import BaseModel


class MCPServerTool(BaseModel):
    server_id: int
    tool_name: str
