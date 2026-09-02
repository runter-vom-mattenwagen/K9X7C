# cURL MCP Server

A Model Context Protocol (MCP) server that provides HTTP request capabilities to Claude Desktop via curl.

> **Disclaimer**: This project was created with assistance from Claude AI (Anthropic). While functional, use at your own discretion and review the code before deployment.

## Features

- Full HTTP method support (GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS)
- Custom headers
- Request body support (JSON, form data, raw text)
- Authentication (Basic Auth, Bearer tokens)
- Configurable timeouts
- Redirect handling
- Clean response parsing (status code, headers, body)

## Why This Tool?

Claude's built-in `web_fetch` tool has limitations:
- Only works with URLs from search results or explicitly mentioned by users
- Only supports GET requests
- No custom headers or authentication
- No POST/PUT/DELETE operations

This MCP server fills that gap, enabling Claude to interact with REST APIs, test endpoints, and make authenticated requests.

## Available Tool

**`curl_request`** - Make HTTP requests with full control

Parameters:
- `url` (required): Target URL
- `method` (optional): HTTP method (default: GET)
- `headers` (optional): Object with header key-value pairs
- `data` (optional): Request body as string
- `auth` (optional): Basic auth as "username:password"
- `bearer_token` (optional): Bearer token for Authorization header
- `timeout` (optional): Timeout in seconds (default: 30)
- `follow_redirects` (optional): Follow redirects (default: true)

## Prerequisites

- Python 3.8 or higher
- Claude Desktop installed
- curl (pre-installed on macOS and most Linux distributions)

## Installation

### 1. Clone or Download

```bash
git clone https://github.com/runter-vom-mattenwagen/curl-mcp-server
cd curl-mcp-server
```

### 2. Configure Claude Desktop

Add the MCP server to your Claude Desktop configuration file.

**Configuration file location by OS:**

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux**: `~/.config/Claude/claude_desktop_config.json`

**Configuration**:

```json
{
  "mcpServers": {
    "curl": {
      "command": "python3",
      "args": ["/absolute/path/to/curl_mcp_server.py"]
    }
  }
}
```

Replace `/absolute/path/to/` with the actual path where you cloned the repository.

### 3. Restart Claude Desktop

After configuration, restart Claude Desktop for the changes to take effect.

## Usage Examples

Once configured, you can make HTTP requests through Claude:

### GET Request
```
User: "Make a GET request to https://api.github.com/users/octocat"
Claude: [uses curl_request tool]
```

### POST with JSON
```
User: "POST this JSON to https://httpbin.org/post: {\"name\": \"test\", \"value\": 123}"
Claude: [uses curl_request with method="POST", data="{...}"]
```

### Authenticated Request
```
User: "Get my repos from GitHub API using bearer token xyz123"
Claude: [uses curl_request with bearer_token]
```

### Custom Headers
```
User: "Make a request to api.example.com with header X-API-Key: secret123"
Claude: [uses curl_request with headers={"X-API-Key": "secret123"}]
```

## Standalone Usage

The `curl_client.py` script can also be used standalone from the command line:

```bash
# Simple GET
python3 curl_client.py https://api.github.com/users/octocat

# POST with JSON
python3 curl_client.py https://httpbin.org/post \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"key":"value"}'

# With authentication
python3 curl_client.py https://api.example.com/data \
  -b "your-bearer-token"

# Basic auth
python3 curl_client.py https://api.example.com/protected \
  -u "username:password"

# JSON output
python3 curl_client.py https://api.example.com/data -j
```

### Options

- `-X, --method`: HTTP method (GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS)
- `-H, --header`: Add header (can be used multiple times)
- `-d, --data`: Request body
- `-u, --auth`: Basic authentication (username:password)
- `-b, --bearer`: Bearer token
- `-t, --timeout`: Timeout in seconds (default: 30)
- `-L, --no-follow-redirects`: Don't follow redirects
- `-v, --verbose`: Verbose output
- `-j, --json-output`: Output as JSON

## Use Cases

### API Development & Testing
- Test REST API endpoints during development
- Verify API responses and status codes
- Test different HTTP methods (POST, PUT, DELETE)

### Webhook Testing
- Send test payloads to webhook endpoints
- Verify webhook handlers

### Authentication Testing
- Test Bearer token authentication
- Verify Basic Auth implementations
- Test API key headers

### Integration Testing
- Test third-party API integrations
- Verify request/response formats
- Check error handling

## Troubleshooting

### curl Not Found

If you get "curl: command not found":
- **macOS/Linux**: curl should be pre-installed. If not: `brew install curl` (macOS) or `apt-get install curl` (Linux)
- **Windows**: Install curl from https://curl.se/windows/

### Request Timeout

If requests are timing out:
- Increase the timeout parameter: `"timeout": 60`
- Check network connectivity
- Verify the target URL is accessible

### Invalid JSON Response

If you get JSON parsing errors:
- The response body might not be JSON
- Check the response headers for Content-Type
- Use verbose mode to see the raw response

## Security Considerations

- **Credentials in Requests**: Never hardcode sensitive credentials. Use environment variables or secure vaults in production.
- **HTTPS Only**: Always use HTTPS URLs when sending sensitive data.
- **Bearer Tokens**: Handle bearer tokens carefully - they provide full access like passwords.
- **Request Logging**: Be cautious about logging requests that contain sensitive data.

## Architecture

- `curl_client.py` - Python wrapper around curl that handles request construction and response parsing
- `curl_mcp_server.py` - MCP protocol server that exposes curl_client as tools for Claude Desktop

The MCP server communicates with Claude Desktop via JSON-RPC over stdin/stdout, executing curl_client as needed.

## Dependencies

- Python 3.8+
- curl (system dependency)

No Python packages required - uses only stdlib.

## License

MIT License - See LICENSE file for details.

## Contributing

Contributions are welcome! Please feel free to submit issues or pull requests.

## Acknowledgments

- Created with assistance from Claude AI (Anthropic)
- Built for the [Model Context Protocol](https://modelcontextprotocol.io/)
