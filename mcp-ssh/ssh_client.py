#!/usr/bin/env python3
"""
SSH Client Wrapper using Paramiko
Provides clean interface for SSH command execution, file upload/download
"""

import paramiko
import os
from pathlib import Path
from typing import Tuple, Optional
import time
import threading

# -------------------------------------------------------------------
# Configuration
# Path to the SSH private key used for all connections.
# Only this key is accepted - SSH agent and other keys are ignored.
# -------------------------------------------------------------------
SSH_KEY_PATH = os.path.expanduser("~/.ssh/claude_ed25519")


class SSHClient:
    """Wrapper around paramiko SSHClient with better error handling"""

    def __init__(self, host: str, user: str = "root", key_path: Optional[str] = None, port: int = 22):
        self.host = host
        self.user = user
        self.port = port
        self.key_path = key_path or SSH_KEY_PATH
        self.client = None

    def is_alive(self) -> bool:
        """Check if the SSH connection is still active."""
        if not self.client:
            return False
        transport = self.client.get_transport()
        if transport is None or not transport.is_active():
            return False
        # Send a cheap keepalive request to verify the connection is truly
        # alive (catches connections dropped silently by firewall/NAT)
        try:
            transport.send_ignore()
            return True
        except Exception:
            return False

    def connect(self) -> None:
        """Establish SSH connection"""
        if self.is_alive():
            return

        # Clean up dead connection if any
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None

        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            key = paramiko.Ed25519Key.from_private_key_file(self.key_path)
            self.client.connect(
                hostname=self.host,
                port=self.port,
                username=self.user,
                pkey=key,
                timeout=10,
                auth_timeout=10,
                banner_timeout=10,
                allow_agent=False,
                look_for_keys=False
            )
            # Enable TCP keepalive to detect dead connections through
            # firewalls/NAT before the next exec_command call
            transport = self.client.get_transport()
            transport.set_keepalive(30)
        except FileNotFoundError:
            raise Exception(f"SSH key not found: {self.key_path}")
        except paramiko.AuthenticationException:
            raise Exception(f"Authentication failed for {self.user}@{self.host}")
        except paramiko.SSHException as e:
            raise Exception(f"SSH connection failed: {str(e)}")
        except Exception as e:
            raise Exception(f"Connection error: {str(e)}")

    def disconnect(self) -> None:
        """Close SSH connection"""
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None

    def exec(self, command: str, timeout: int = 30) -> Tuple[str, str, int]:
        """
        Execute command on remote host

        Returns:
            Tuple of (stdout, stderr, exit_code)
        """
        self.connect()

        try:
            stdin, stdout, stderr = self.client.exec_command(
                command,
                timeout=timeout,
                get_pty=False
            )

            # Read stdout and stderr in parallel to avoid deadlock
            # when buffers fill up (64KB limit per channel)
            stdout_chunks = []
            stderr_chunks = []

            def read_stdout():
                stdout_chunks.append(stdout.read().decode('utf-8', errors='replace'))

            def read_stderr():
                stderr_chunks.append(stderr.read().decode('utf-8', errors='replace'))

            t1 = threading.Thread(target=read_stdout)
            t2 = threading.Thread(target=read_stderr)
            t1.start()
            t2.start()
            t1.join(timeout=timeout)
            t2.join(timeout=timeout)

            stdout_str = stdout_chunks[0] if stdout_chunks else ''
            stderr_str = stderr_chunks[0] if stderr_chunks else ''

            # recv_exit_status() blocks indefinitely if threads timed out
            # without consuming all output. Use channel ready-check with
            # a hard deadline instead.
            deadline = time.time() + timeout
            channel = stdout.channel
            while not channel.exit_status_ready():
                if time.time() > deadline:
                    channel.close()
                    raise Exception(f"Command timed out after {timeout}s")
                time.sleep(0.05)
            exit_code = channel.recv_exit_status()

            return stdout_str, stderr_str, exit_code

        except paramiko.SSHException as e:
            raise Exception(f"Command execution failed: {str(e)}")
        except Exception as e:
            raise Exception(f"Execution error: {str(e)}")

    def upload(self, local_path: str, remote_path: str) -> None:
        """Upload file to remote host using SFTP"""
        self.connect()

        try:
            sftp = self.client.open_sftp()

            remote_dir = os.path.dirname(remote_path)
            if remote_dir:
                try:
                    sftp.stat(remote_dir)
                except FileNotFoundError:
                    self._mkdir_p(sftp, remote_dir)

            sftp.put(local_path, remote_path)
            sftp.close()

        except FileNotFoundError:
            raise Exception(f"Local file not found: {local_path}")
        except Exception as e:
            raise Exception(f"Upload failed: {str(e)}")

    def download(self, remote_path: str, local_path: str) -> None:
        """Download file from remote host using SFTP"""
        self.connect()

        try:
            sftp = self.client.open_sftp()

            local_dir = os.path.dirname(local_path)
            if local_dir:
                os.makedirs(local_dir, exist_ok=True)

            sftp.get(remote_path, local_path)
            sftp.close()

        except FileNotFoundError:
            raise Exception(f"Remote file not found: {remote_path}")
        except Exception as e:
            raise Exception(f"Download failed: {str(e)}")

    def _mkdir_p(self, sftp, remote_dir: str) -> None:
        """Create directory recursively via SFTP"""
        dirs = []
        while remote_dir and remote_dir != '/':
            try:
                sftp.stat(remote_dir)
                break
            except FileNotFoundError:
                dirs.append(remote_dir)
                remote_dir = os.path.dirname(remote_dir)

        for directory in reversed(dirs):
            sftp.mkdir(directory)

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()


def test_connection():
    """Test SSH connection"""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python3 ssh_client.py <host> [user] [command]")
        sys.exit(1)

    host = sys.argv[1]
    user = sys.argv[2] if len(sys.argv) > 2 else "root"
    command = sys.argv[3] if len(sys.argv) > 3 else "uptime"

    try:
        with SSHClient(host, user) as ssh:
            stdout, stderr, exit_code = ssh.exec(command)
            print(f"Exit Code: {exit_code}")
            print(f"STDOUT:\n{stdout}")
            if stderr:
                print(f"STDERR:\n{stderr}")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    test_connection()
