#!/usr/bin/env python3
"""
SSH MCP Server
Provides SSH command execution capabilities via MCP protocol
"""

import json
import sys
import os
import atexit
from pathlib import Path
from ssh_client import SSHClient


# ---------------------------------------------------------------------------
# Connection pool – reuses SSH connections per user@host to avoid
# rapid connect/disconnect cycles that can trigger sshd rate limiting
# (MaxStartups) and reduce latency on sequential calls.
# ---------------------------------------------------------------------------
_pool: dict[str, SSHClient] = {}


def _pool_get(host: str, user: str) -> SSHClient:
    """Get or create a pooled SSH connection."""
    key = f"{user}@{host}"
    ssh = _pool.get(key)
    if ssh and ssh.is_alive():
        return ssh
    # Old connection dead or missing – create new one
    if ssh:
        try:
            ssh.disconnect()
        except Exception:
            pass
    ssh = SSHClient(host, user)
    ssh.connect()
    _pool[key] = ssh
    return ssh


def _pool_cleanup():
    """Disconnect all pooled connections on exit."""
    for ssh in _pool.values():
        try:
            ssh.disconnect()
        except Exception:
            pass
    _pool.clear()


atexit.register(_pool_cleanup)


def log(msg: str) -> None:
    """Log to stderr (visible in Claude Desktop MCP logs)."""
    print(f"[ssh-mcp] {msg}", file=sys.stderr, flush=True)


def send_response(response):
    """Send JSON-RPC response via stdout"""
    print(json.dumps(response), flush=True)


def send_error(request_id, code: int, message: str):
    """Send JSON-RPC error response"""
    send_response({
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": code,
            "message": message
        }
    })


def send_result(request_id, text: str):
    """Send JSON-RPC success response with text content"""
    send_response({
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": text
                }
            ]
        }
    })


def handle_initialize(request):
    """Initialize handshake"""
    send_response({
        "jsonrpc": "2.0",
        "id": request.get("id"),
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {}
            },
            "serverInfo": {
                "name": "ssh-mcp-server",
                "version": "1.1.0"
            }
        }
    })


def handle_tools_list(request):
    """Return available tools"""
    tools = [
        {
            "name": "ssh_exec",
            "description": "Execute a command on a remote host via SSH",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "host": {
                        "type": "string",
                        "description": "Target host (hostname or IP)"
                    },
                    "command": {
                        "type": "string",
                        "description": "Command to execute"
                    },
                    "user": {
                        "type": "string",
                        "description": "SSH user (default: root)",
                        "default": "root"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Command timeout in seconds (default: 30)",
                        "default": 30
                    }
                },
                "required": ["host", "command"]
            }
        },
        {
            "name": "ssh_upload",
            "description": "Upload a file to remote host via SFTP",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "host": {
                        "type": "string",
                        "description": "Target host (hostname or IP)"
                    },
                    "local_path": {
                        "type": "string",
                        "description": "Local file path"
                    },
                    "remote_path": {
                        "type": "string",
                        "description": "Remote destination path"
                    },
                    "user": {
                        "type": "string",
                        "description": "SSH user (default: root)",
                        "default": "root"
                    }
                },
                "required": ["host", "local_path", "remote_path"]
            }
        },
        {
            "name": "ssh_download",
            "description": "Download a file from remote host via SFTP",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "host": {
                        "type": "string",
                        "description": "Target host (hostname or IP)"
                    },
                    "remote_path": {
                        "type": "string",
                        "description": "Remote file path"
                    },
                    "local_path": {
                        "type": "string",
                        "description": "Local destination path"
                    },
                    "user": {
                        "type": "string",
                        "description": "SSH user (default: root)",
                        "default": "root"
                    }
                },
                "required": ["host", "remote_path", "local_path"]
            }
        }
    ]

    send_response({
        "jsonrpc": "2.0",
        "id": request.get("id"),
        "result": {
            "tools": tools
        }
    })


def handle_tools_call(request):
    """Execute tool"""
    params = request.get("params", {})
    tool_name = params.get("name")
    arguments = params.get("arguments", {})
    request_id = request.get("id")

    try:
        if tool_name == "ssh_exec":
            host = arguments["host"]
            command = arguments["command"]
            user = arguments.get("user", "root")
            timeout = arguments.get("timeout", 30)

            ssh = _pool_get(host, user)
            stdout, stderr, exit_code = ssh.exec(command, timeout)

            output = f"Exit Code: {exit_code}\n"
            if stdout:
                output += f"\nSTDOUT:\n{stdout}"
            if stderr:
                output += f"\nSTDERR:\n{stderr}"

            send_result(request_id, output)

        elif tool_name == "ssh_upload":
            host = arguments["host"]
            local_path = os.path.expanduser(arguments["local_path"])
            remote_path = arguments["remote_path"]
            user = arguments.get("user", "root")

            ssh = _pool_get(host, user)
            ssh.upload(local_path, remote_path)

            send_result(request_id, f"Successfully uploaded {local_path} to {host}:{remote_path}")

        elif tool_name == "ssh_download":
            host = arguments["host"]
            remote_path = arguments["remote_path"]
            local_path = os.path.expanduser(arguments["local_path"])
            user = arguments.get("user", "root")

            ssh = _pool_get(host, user)
            ssh.download(remote_path, local_path)

            send_result(request_id, f"Successfully downloaded {host}:{remote_path} to {local_path}")

        else:
            send_error(request_id, -32601, f"Unknown tool: {tool_name}")

    except Exception as e:
        log(f"Tool {tool_name} failed: {e}")
        # Evict broken connection from pool
        host = arguments.get("host", "")
        user = arguments.get("user", "root")
        key = f"{user}@{host}"
        if key in _pool:
            try:
                _pool[key].disconnect()
            except Exception:
                pass
            del _pool[key]
        send_error(request_id, -32000, str(e))


def main():
    """Main loop - reads JSON-RPC from stdin"""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        request_id = None
        try:
            request = json.loads(line)
            request_id = request.get("id")
            method = request.get("method")

            if method == "initialize":
                handle_initialize(request)
            elif method == "notifications/initialized":
                continue
            elif method == "tools/list":
                handle_tools_list(request)
            elif method == "tools/call":
                handle_tools_call(request)
            else:
                if request_id is not None:
                    send_error(request_id, -32601, f"Method not found: {method}")

        except json.JSONDecodeError as e:
            log(f"JSON parse error: {e}")
            # Can't respond without a valid request id
            continue
        except Exception as e:
            log(f"Unhandled error: {e}")
            if request_id is not None:
                send_error(request_id, -32000, f"Internal error: {e}")


if __name__ == "__main__":
    main()
