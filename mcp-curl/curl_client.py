#!/usr/bin/env python3
"""
cURL Wrapper for HTTP Requests
Provides a simple interface for making HTTP requests via curl
"""

import sys
import json
import subprocess
import argparse
from typing import Dict, Optional


def make_request(
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    data: Optional[str] = None,
    auth: Optional[str] = None,
    bearer_token: Optional[str] = None,
    timeout: int = 30,
    follow_redirects: bool = True,
    verbose: bool = False
) -> Dict:
    """
    Make an HTTP request using curl
    
    Args:
        url: Target URL
        method: HTTP method (GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS)
        headers: Dictionary of headers
        data: Request body (string, will be sent as-is)
        auth: Basic auth in format "username:password"
        bearer_token: Bearer token for Authorization header
        timeout: Request timeout in seconds
        follow_redirects: Follow HTTP redirects
        verbose: Include response headers in output
    
    Returns:
        Dictionary with status_code, headers, and body
    """
    
    cmd = ["curl", "-s", "-k"]  # Silent mode, allow insecure SSL
    
    # Method
    cmd.extend(["-X", method.upper()])
    
    # Include response headers
    cmd.append("-i")
    
    # Timeout
    cmd.extend(["--max-time", str(timeout)])
    
    # Follow redirects
    if follow_redirects:
        cmd.append("-L")
    
    # Authentication
    if bearer_token:
        cmd.extend(["-H", f"Authorization: Bearer {bearer_token}"])
    elif auth:
        cmd.extend(["-u", auth])
    
    # Headers
    if headers:
        for key, value in headers.items():
            cmd.extend(["-H", f"{key}: {value}"])
    
    # Data/Body
    if data:
        cmd.extend(["-d", data])
    
    # URL (last)
    cmd.append(url)
    
    if verbose:
        print(f"Executing: {' '.join(cmd)}", file=sys.stderr)
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 5  # Add buffer to subprocess timeout
        )
        
        if result.returncode != 0:
            return {
                "error": f"curl failed with exit code {result.returncode}",
                "stderr": result.stderr,
                "status_code": None,
                "headers": {},
                "body": ""
            }
        
        # Parse response (headers + body)
        response_text = result.stdout
        
        # Split headers and body
        parts = response_text.split('\r\n\r\n', 1)
        if len(parts) == 2:
            headers_text, body = parts
        else:
            # No body or different line ending
            parts = response_text.split('\n\n', 1)
            if len(parts) == 2:
                headers_text, body = parts
            else:
                headers_text = response_text
                body = ""
        
        # Parse status line and headers
        header_lines = headers_text.split('\n')
        status_line = header_lines[0] if header_lines else ""
        
        # Extract status code
        status_code = None
        if status_line.startswith('HTTP/'):
            parts = status_line.split(' ', 2)
            if len(parts) >= 2:
                try:
                    status_code = int(parts[1])
                except ValueError:
                    pass
        
        # Parse headers into dict
        response_headers = {}
        for line in header_lines[1:]:
            if ':' in line:
                key, value = line.split(':', 1)
                response_headers[key.strip()] = value.strip()
        
        return {
            "status_code": status_code,
            "headers": response_headers,
            "body": body.strip()
        }
        
    except subprocess.TimeoutExpired:
        return {
            "error": f"Request timed out after {timeout} seconds",
            "status_code": None,
            "headers": {},
            "body": ""
        }
    except Exception as e:
        return {
            "error": str(e),
            "status_code": None,
            "headers": {},
            "body": ""
        }


def main():
    parser = argparse.ArgumentParser(description="cURL Wrapper for HTTP Requests")
    parser.add_argument("url", help="Target URL")
    parser.add_argument("-X", "--method", default="GET", 
                       help="HTTP method (GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS)")
    parser.add_argument("-H", "--header", action="append", dest="headers",
                       help="Header in format 'Key: Value' (can be used multiple times)")
    parser.add_argument("-d", "--data", help="Request body")
    parser.add_argument("-u", "--auth", help="Basic auth in format 'username:password'")
    parser.add_argument("-b", "--bearer", dest="bearer_token", help="Bearer token")
    parser.add_argument("-t", "--timeout", type=int, default=30, help="Timeout in seconds")
    parser.add_argument("-L", "--no-follow-redirects", action="store_true",
                       help="Do not follow redirects")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("-j", "--json-output", action="store_true", 
                       help="Output response as JSON")
    
    args = parser.parse_args()
    
    # Parse headers
    headers_dict = {}
    if args.headers:
        for header in args.headers:
            if ':' in header:
                key, value = header.split(':', 1)
                headers_dict[key.strip()] = value.strip()
    
    # Make request
    response = make_request(
        url=args.url,
        method=args.method,
        headers=headers_dict,
        data=args.data,
        auth=args.auth,
        bearer_token=args.bearer_token,
        timeout=args.timeout,
        follow_redirects=not args.no_follow_redirects,
        verbose=args.verbose
    )
    
    # Output
    if args.json_output:
        print(json.dumps(response, indent=2))
    else:
        if "error" in response:
            print(f"Error: {response['error']}", file=sys.stderr)
            if response.get('stderr'):
                print(f"stderr: {response['stderr']}", file=sys.stderr)
            sys.exit(1)
        
        print(f"Status: {response['status_code']}")
        print(f"\nHeaders:")
        for key, value in response['headers'].items():
            print(f"  {key}: {value}")
        print(f"\nBody:")
        print(response['body'])


if __name__ == '__main__':
    main()
