# SSH MCP Server

A Model Context Protocol (MCP) server that provides SSH command execution and SFTP file transfer capabilities to Claude Desktop.

> **Disclaimer**: This project was created with assistance from Claude AI (Anthropic). While functional, use at your own discretion and review the code before deployment.

## Features

- **Remote Command Execution**: Execute bash commands on remote hosts via SSH
- **SFTP File Operations**: Upload and download files
- **Multi-Host Support**: Connect to different servers with unique credentials
- **Key-Based Authentication**: Secure SSH key authentication
- **Timeout Control**: Configurable command timeouts
- **Clean Error Handling**: Informative error messages

## Why This Tool?

This MCP server bridges Claude Desktop with your remote infrastructure, enabling natural language server administration. Perfect for:

- **Homelab Management**: Administer Docker hosts, update configs, check logs
- **DevOps Tasks**: Quick server checks, deployments, troubleshooting
- **Remote Debugging**: Execute diagnostic commands, download logs
- **File Management**: Transfer files between local and remote systems

## Available Tools

### `ssh_exec`
Execute commands on remote hosts

**Parameters:**
- `command` (required): Command to execute
- `host` (required): Target hostname or IP address
- `user` (optional): SSH user (default: root)
- `timeout` (optional): Command timeout in seconds (default: 30)

### `ssh_upload`
Upload files to remote hosts via SFTP

**Parameters:**
- `local_path` (required): Local file path
- `remote_path` (required): Remote destination path
- `host` (required): Target hostname or IP
- `user` (optional): SSH user (default: root)

### `ssh_download`
Download files from remote hosts via SFTP

**Parameters:**
- `remote_path` (required): Remote file path
- `local_path` (required): Local destination path
- `host` (required): Target hostname or IP
- `user` (optional): SSH user (default: root)

## Prerequisites

- Python 3.10 or higher
- Claude Desktop installed
- SSH access to target hosts
- SSH key for authentication (Ed25519 recommended)
- [uv](https://github.com/astral-sh/uv) (recommended) or pip for Python package management

## Installation

### 1. Clone or Download

```bash
git clone https://github.com/runter-vom-mattenwagen/ssh-mcp-server
cd ssh-mcp-server
```

### 2. Setup SSH Authentication

This server uses SSH key-based authentication. The key is configured via the `SSH_KEY_PATH` variable at the top of `ssh_client.py`.

The server explicitly disables SSH agent forwarding and automatic key discovery (`allow_agent=False`, `look_for_keys=False`). Only the configured key is accepted — no other keys in `~/.ssh/` or loaded in the SSH agent will be tried.

**Configure the key path** in `ssh_client.py`:

```python
# -------------------------------------------------------------------
# Configuration
# Path to the SSH private key used for all connections.
# Only this key is accepted - SSH agent and other keys are ignored.
# -------------------------------------------------------------------
SSH_KEY_PATH = os.path.expanduser("~/.ssh/claude_ed25519")
```

**Generate a new key (if needed):**

```bash
ssh-keygen -t ed25519 -f ~/.ssh/claude_ed25519 -C "claude-mcp"
```

**Copy key to remote hosts:**

```bash
ssh-copy-id -i ~/.ssh/claude_ed25519.pub user@remote-host
```

### 3. Configure Claude Desktop

Add the MCP server to your Claude Desktop configuration file.

**Configuration file location by OS:**

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux**: `~/.config/Claude/claude_desktop_config.json`

**Configuration:**

#### Option A: Using uv (Recommended)

```json
{
  "mcpServers": {
    "ssh": {
      "command": "uv",
      "args": [
        "run",
        "/absolute/path/to/ssh_mcp_server.py"
      ]
    }
  }
}
```

#### Option B: Using system Python

```json
{
  "mcpServers": {
    "ssh": {
      "command": "python3",
      "args": [
        "/absolute/path/to/ssh_mcp_server.py"
      ]
    }
  }
}
```

**Note**: If using system Python, you must install dependencies first:

```bash
pip3 install paramiko
```

### 4. Restart Claude Desktop

After configuration, restart Claude Desktop for the changes to take effect.

## Usage Examples

Once configured, you can interact with remote hosts through Claude using natural language:

### Command Execution

```
User: "Check running Docker containers on my server"
Claude: [uses ssh_exec with command="docker ps"]

User: "What's the disk usage on myserver?"
Claude: [uses ssh_exec with command="df -h"]

User: "Restart the Traefik container"
Claude: [uses ssh_exec with command="cd /path && docker compose restart"]
```

### File Operations

```
User: "Upload my local config.yml to the server at /etc/app/config.yml"
Claude: [uses ssh_upload]

User: "Download the nginx error log from /var/log/nginx/error.log"
Claude: [uses ssh_download]
```

### Multi-Step Workflows

```
User: "Check if nginx is running, and if not, start it"
Claude: [executes systemctl status nginx, then starts if needed]

User: "Find large log files on the server and show me the top 5"
Claude: [executes find + du commands, parses results]
```

## Standalone Usage

The `ssh_client.py` can also be used as a standalone module:

```python
from ssh_client import SSHClient

# Command execution
client = SSHClient(host="192.168.1.100", user="root")
exit_code, stdout, stderr = client.execute("ls -la /var/www")
print(f"Output: {stdout}")

# File upload
client.upload_file("/local/file.txt", "/remote/path/file.txt")

# File download
client.download_file("/remote/path/data.log", "/local/downloads/data.log")

client.close()
```

## Homelab Integration Example

This MCP server is particularly useful for homelab management. Example workflow for a Docker-based infrastructure:

```
User: "Deploy a new Nextcloud instance on my homelab server"

Claude will:
1. Create directory structure via ssh_exec
2. Generate docker-compose.yml via ssh_exec with heredoc
3. Update DNS records via ssh_exec
4. Start the container via ssh_exec
5. Verify it's running via ssh_exec
```

## Security Considerations

- **Key Isolation**: The server exclusively uses the key configured in `SSH_KEY_PATH`. SSH agent and all other keys in `~/.ssh/` are explicitly ignored (`allow_agent=False`, `look_for_keys=False`).
- **SSH Keys**: Store private keys securely with restrictive permissions (`chmod 600`)
- **Key Rotation**: Regularly rotate SSH keys
- **Limited Scope**: Consider creating dedicated SSH users with restricted permissions
- **Host Verification**: paramiko does **not** read `~/.ssh/config`. The server uses `AutoAddPolicy()` (TOFU behavior). For strict host verification: switch to `RejectPolicy` and pre-populate a `known_hosts` file, or at minimum verify the host fingerprint after the first connect
- **Command Validation**: Review commands before Claude executes them

## Troubleshooting

### Connection Failed

**Check SSH connectivity:**
```bash
ssh -i ~/.ssh/claude_ed25519 user@host
```

**Verify SSH config:**
```bash
cat ~/.ssh/config | grep -A5 myserver
```

**Check key permissions:**
```bash
chmod 600 ~/.ssh/claude_ed25519
```

### Authentication Failed

- Ensure SSH key is copied to remote host (`ssh-copy-id`)
- Verify correct user has key in `~/.ssh/authorized_keys`
- Verify `SSH_KEY_PATH` in `ssh_client.py` points to the correct private key
- Test directly: `ssh -i ~/.ssh/claude_ed25519 -o IdentitiesOnly=yes user@host`

### Permission Denied

- User lacks permissions for the command
- Check sudo requirements
- Verify file/directory permissions on remote host

### Command Timeout

- Command takes too long to execute
- Increase timeout parameter in request
- Check network connectivity
- Consider running long commands in background

### MCP Server Not Starting

**Test the server directly:**
```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | \
  uv run /path/to/ssh_mcp_server.py
```

**Check Claude Desktop logs for errors**

**Verify paramiko is installed:**
```bash
python3 -c "import paramiko; print(paramiko.__version__)"
```

## Architecture

- `ssh_client.py` - Paramiko wrapper providing SSH/SFTP operations
- `ssh_mcp_server.py` - MCP protocol server exposing tools to Claude Desktop

The MCP server communicates with Claude Desktop via JSON-RPC over stdin/stdout, executing ssh_client operations as needed.

## Dependencies

- Python 3.10+
- [paramiko](https://www.paramiko.org/) - SSH2 protocol library

## Use Cases

### Infrastructure Management
- Check server status and resource usage
- Manage Docker containers and services
- Update configurations
- Monitor logs

### Deployment Automation
- Deploy applications
- Update DNS records
- Configure reverse proxies
- Manage SSL certificates

### Troubleshooting
- Execute diagnostic commands
- Download log files for analysis
- Check service status
- Verify configurations

### File Management
- Transfer configuration files
- Backup and restore operations
- Deploy application updates
- Sync files between systems

## Related Tools

- **openssl-mcp-server**: Certificate management for your infrastructure
- **curl-mcp-server**: HTTP/REST API testing
- **nextcloud-mcp-server**: Cloud storage integration

## License

MIT License - See LICENSE file for details.

## Contributing

Contributions are welcome! Please feel free to submit issues or pull requests.

## Acknowledgments

- Created with assistance from Claude AI (Anthropic)
- Uses [Paramiko](https://www.paramiko.org/) for SSH operations
- Built for the [Model Context Protocol](https://modelcontextprotocol.io/)
