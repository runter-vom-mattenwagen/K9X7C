#!/usr/bin/env python3
"""
cURL MCP Server for Claude Desktop
Provides HTTP request capabilities via curl
"""

import json
import sys
import subprocess
from pathlib import Path

CURL_CLIENT = Path(__file__).parent / "curl_client.py"


def send_response(response):
    """Send JSON-RPC response via stdout"""
    print(json.dumps(response), flush=True)


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
                "name": "curl",
                "version": "1.0.0"
            }
        }
    })


def handle_tools_list(request):
    """Return available tools"""
    tools = [
        {
            "name": "curl_request",
            "description": "Make an HTTP request using curl. Supports all HTTP methods, custom headers, authentication, and request bodies. Perfect for API testing and REST API interactions.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Target URL (required)"
                    },
                    "method": {
                        "type": "string",
                        "description": "HTTP method: GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS (default: GET)",
                        "enum": ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
                        "default": "GET"
                    },
                    "headers": {
                        "type": "object",
                        "description": "HTTP headers as key-value pairs (optional)",
                        "additionalProperties": {
                            "type": "string"
                        }
                    },
                    "data": {
                        "type": "string",
                        "description": "Request body (optional). Can be JSON string, form data, or any raw text"
                    },
                    "auth": {
                        "type": "string",
                        "description": "Basic authentication in format 'username:password' (optional)"
                    },
                    "bearer_token": {
                        "type": "string",
                        "description": "Bearer token for Authorization header (optional)"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Request timeout in seconds (default: 30)",
                        "default": 30
                    },
                    "follow_redirects": {
                        "type": "boolean",
                        "description": "Follow HTTP redirects (default: true)",
                        "default": True
                    }
                },
                "required": ["url"]
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
    
    if tool_name != "curl_request":
        send_response({
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "error": {
                "code": -32601,
                "message": f"Unknown tool: {tool_name}"
            }
        })
        return
    
    # Build curl_client command
    cmd = ["python3", str(CURL_CLIENT)]
    
    # Required: URL
    url = arguments.get("url")
    if not url:
        send_response({
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "error": {
                "code": -32602,
                "message": "Missing required parameter: url"
            }
        })
        return
    
    cmd.append(url)
    
    # Optional: Method
    if "method" in arguments:
        cmd.extend(["-X", arguments["method"]])
    
    # Optional: Headers
    if "headers" in arguments and isinstance(arguments["headers"], dict):
        for key, value in arguments["headers"].items():
            cmd.extend(["-H", f"{key}: {value}"])
    
    # Optional: Data
    if "data" in arguments:
        cmd.extend(["-d", arguments["data"]])
    
    # Optional: Auth
    if "auth" in arguments:
        cmd.extend(["-u", arguments["auth"]])
    
    # Optional: Bearer token
    if "bearer_token" in arguments:
        cmd.extend(["-b", arguments["bearer_token"]])
    
    # Optional: Timeout
    if "timeout" in arguments:
        cmd.extend(["-t", str(arguments["timeout"])])
    
    # Optional: Follow redirects
    if "follow_redirects" in arguments and not arguments["follow_redirects"]:
        cmd.append("-L")
    
    # Always request JSON output
    cmd.append("-j")
    
    # Execute
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=arguments.get("timeout", 30) + 5
        )
        
        if result.returncode != 0:
            send_response({
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "error": {
                    "code": -32000,
                    "message": f"Request failed: {result.stderr}"
                }
            })
            return
        
        # Parse JSON response from curl_client
        try:
            response_data = json.loads(result.stdout)
            
            # Format output nicely
            output_lines = []
            
            if "error" in response_data:
                output_lines.append(f"Error: {response_data['error']}")
                if response_data.get('stderr'):
                    output_lines.append(f"Details: {response_data['stderr']}")
            else:
                output_lines.append(f"Status: {response_data.get('status_code', 'Unknown')}")
                
                if response_data.get('headers'):
                    output_lines.append("\nHeaders:")
                    for key, value in response_data['headers'].items():
                        output_lines.append(f"  {key}: {value}")
                
                if response_data.get('body'):
                    output_lines.append("\nResponse Body:")
                    output_lines.append(response_data['body'])
            
            send_response({
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": "\n".join(output_lines)
                        }
                    ]
                }
            })
            
        except json.JSONDecodeError:
            send_response({
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "error": {
                    "code": -32000,
                    "message": f"Failed to parse response: {result.stdout}"
                }
            })
            
    except subprocess.TimeoutExpired:
        send_response({
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "error": {
                "code": -32000,
                "message": "Request timeout"
            }
        })
    except Exception as e:
        send_response({
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "error": {
                "code": -32000,
                "message": str(e)
            }
        })


def main():
    """Main loop - reads JSON-RPC from stdin"""
    for line in sys.stdin:
        try:
            request = json.loads(line)
            method = request.get("method")
            
            if method == "initialize":
                handle_initialize(request)
            elif method == "notifications/initialized":
                # Client confirms initialization - no response needed
                continue
            elif method == "tools/list":
                handle_tools_list(request)
            elif method == "tools/call":
                handle_tools_call(request)
            else:
                # Only send error if there's an ID (request, not notification)
                if request.get("id") is not None:
                    send_response({
                        "jsonrpc": "2.0",
                        "id": request.get("id"),
                        "error": {
                            "code": -32601,
                            "message": f"Method not found: {method}"
                        }
                    })
        except json.JSONDecodeError:
            continue
        except Exception:
            continue


if __name__ == "__main__":
    main()
