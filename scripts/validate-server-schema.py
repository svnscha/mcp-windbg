#!/usr/bin/env python3
"""
Validates server.json against the MCP server schema.

This script:
1. Reads server.json and extracts the $schema URL
2. Downloads the JSON schema from the URL
3. Validates server.json against the schema
4. Reports any validation errors

Usage:
    uv run scripts/validate-server-schema.py
    python scripts/validate-server-schema.py
"""

import json
import sys
import urllib.request
from pathlib import Path


def main() -> int:
    """Main entry point for schema validation."""
    # Find server.json in the repository root
    script_dir = Path(__file__).parent
    repo_root = script_dir.parent
    server_json_path = repo_root / "server.json"

    if not server_json_path.exists():
        print(f"ERROR: server.json not found at {server_json_path}", file=sys.stderr)
        return 1

    print(f"Validating {server_json_path}...")

    # Load server.json
    try:
        with open(server_json_path, "r", encoding="utf-8") as f:
            server_data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in server.json: {e}", file=sys.stderr)
        return 1

    # Extract schema URL
    schema_url = server_data.get("$schema")
    if not schema_url:
        print("ERROR: No $schema field found in server.json", file=sys.stderr)
        return 1

    print(f"Schema URL: {schema_url}")

    # Download the schema
    print("Downloading schema...")
    try:
        with urllib.request.urlopen(schema_url, timeout=30) as response:
            schema_data = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as e:
        print(f"ERROR: Failed to download schema: {e}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in schema: {e}", file=sys.stderr)
        return 1

    print("Schema downloaded successfully")

    # Try to import jsonschema for validation
    try:
        import jsonschema
        from jsonschema import Draft7Validator, ValidationError
    except ImportError:
        print(
            "WARNING: jsonschema not installed, performing basic structural validation only",
            file=sys.stderr,
        )
        return validate_basic(server_data)

    # Validate against schema
    print("Validating against schema...")
    validator = Draft7Validator(schema_data)
    errors = list(validator.iter_errors(server_data))

    if errors:
        print(f"\nERROR: Schema validation failed with {len(errors)} error(s):\n", file=sys.stderr)
        for i, error in enumerate(errors, 1):
            path = " -> ".join(str(p) for p in error.absolute_path) or "(root)"
            print(f"  {i}. Path: {path}", file=sys.stderr)
            print(f"     Message: {error.message}", file=sys.stderr)
            print(file=sys.stderr)
        return 1

    print("\n[OK] server.json is valid against the MCP server schema")
    return 0


def validate_basic(server_data: dict) -> int:
    """Perform basic structural validation without jsonschema library."""
    errors = []

    # Check required fields
    required_fields = ["name", "description", "version"]
    for field in required_fields:
        if field not in server_data:
            errors.append(f"Missing required field: {field}")

    # Check name format (reverse-DNS with one slash)
    name = server_data.get("name", "")
    if name and "/" not in name:
        errors.append(f"Name '{name}' should be in reverse-DNS format with a slash (e.g., 'io.github.user/project')")

    # Check packages array
    packages = server_data.get("packages", [])
    if not isinstance(packages, list):
        errors.append("'packages' should be an array")
    else:
        for i, pkg in enumerate(packages):
            if not isinstance(pkg, dict):
                errors.append(f"Package {i} should be an object")
                continue

            pkg_required = ["registryType", "identifier", "transport"]
            for field in pkg_required:
                if field not in pkg:
                    errors.append(f"Package {i} missing required field: {field}")

            transport = pkg.get("transport", {})
            if isinstance(transport, dict):
                transport_type = transport.get("type")
                if transport_type not in ["stdio", "streamable-http", "sse"]:
                    errors.append(f"Package {i} has invalid transport type: {transport_type}")

                # streamable-http requires url
                if transport_type == "streamable-http" and "url" not in transport:
                    errors.append(f"Package {i} with streamable-http transport requires 'url' field")

    if errors:
        print(f"\nERROR: Basic validation failed with {len(errors)} error(s):\n", file=sys.stderr)
        for i, error in enumerate(errors, 1):
            print(f"  {i}. {error}", file=sys.stderr)
        return 1

    print("\n[OK] server.json passes basic structural validation")
    print("  (Install 'jsonschema' for full schema validation)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
