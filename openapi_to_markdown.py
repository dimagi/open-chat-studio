#!/usr/bin/env python3
"""
OpenAPI Schema to Markdown Documentation Converter

This module provides functionality to convert OpenAPI 3.x schemas into
self-contained markdown documents for each API endpoint.
"""

import json
import re
from pathlib import Path
from typing import Any

import yaml


class OpenAPIToMarkdownConverter:
    """Converts OpenAPI schemas to markdown documentation."""

    def __init__(self, schema: str | dict[str, Any] | Path):
        """
        Initialize the converter with an OpenAPI schema.

        Args:
            schema: OpenAPI schema as JSON/YAML string, dict, or file path
        """
        if isinstance(schema, str | Path):
            self.schema = self._load_schema(schema)
        else:
            self.schema = schema

        self.base_info = self._extract_base_info()
        self.components = self.schema.get("components", {})
        self.schemas = self.components.get("schemas", {})

    def _load_schema(self, schema_path: str | Path) -> dict[str, Any]:
        """Load OpenAPI schema from file path or URL string."""
        path = Path(schema_path)

        if not path.exists():
            # Try to parse as JSON/YAML string
            try:
                return json.loads(str(schema_path))
            except json.JSONDecodeError:
                try:
                    return yaml.safe_load(str(schema_path))
                except yaml.YAMLError:
                    raise ValueError("Invalid schema: not a valid file path, JSON, or YAML") from None

        # Load from file
        content = path.read_text(encoding="utf-8")

        if path.suffix.lower() in [".yaml", ".yml"]:
            return yaml.safe_load(content)
        elif path.suffix.lower() == ".json":
            return json.loads(content)
        else:
            # Try to auto-detect format
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return yaml.safe_load(content)

    def _extract_base_info(self) -> dict[str, Any]:
        """Extract basic API information from schema."""
        info = self.schema.get("info", {})
        servers = self.schema.get("servers", [])

        return {
            "title": info.get("title", "API Documentation"),
            "version": info.get("version", "1.0.0"),
            "description": info.get("description", ""),
            "servers": servers,
            "base_url": servers[0].get("url", "") if servers else "",
        }

    def convert_to_markdown_files(self, output_dir: str | Path = "api_docs") -> list[str]:
        """
        Convert OpenAPI schema to markdown files grouped by tags.

        Args:
            output_dir: Directory to save markdown files

        Returns:
            List of generated file paths
        """
        output_path = Path(output_dir)
        output_path.mkdir(exist_ok=True)

        # Group endpoints by tags
        tag_groups = self._group_endpoints_by_tag()

        generated_files = []
        for tag, endpoints in tag_groups.items():
            filename = self._generate_tag_filename(tag)
            file_path = output_path / f"{filename}.md"

            markdown_content = self._generate_tag_markdown(tag, endpoints)

            file_path.write_text(markdown_content, encoding="utf-8")
            generated_files.append(str(file_path))

        return generated_files

    def _group_endpoints_by_tag(self) -> dict[str, list[dict[str, Any]]]:
        """
        Group all endpoints by their tags.

        Returns:
            Dictionary mapping tag names to lists of endpoint info
        """
        tag_groups = {}
        paths = self.schema.get("paths", {})

        for path, path_item in paths.items():
            for method, operation in path_item.items():
                if method.lower() in ["get", "post", "put", "patch", "delete", "options", "head"]:
                    tags = operation.get("tags", ["Untagged"])

                    # Handle endpoints with multiple tags
                    for tag in tags:
                        if tag not in tag_groups:
                            tag_groups[tag] = []

                        endpoint_info = {"method": method, "path": path, "operation": operation}
                        tag_groups[tag].append(endpoint_info)

        return tag_groups

    def _generate_tag_filename(self, tag: str) -> str:
        """Generate a clean filename for the tag."""
        # Clean up tag name for filename
        clean_tag = re.sub(r"[^a-zA-Z0-9_-]", "_", tag.lower())
        clean_tag = re.sub(r"_+", "_", clean_tag)
        return clean_tag.strip("_")

    def _generate_filename(self, method: str, path: str, operation: dict[str, Any]) -> str:
        """Generate a clean filename for the endpoint (legacy method)."""
        # Use operationId if available, otherwise generate from method and path
        if "operationId" in operation:
            base_name = operation["operationId"]
        else:
            # Clean up path and combine with method
            clean_path = re.sub(r"[^a-zA-Z0-9_-]", "_", path.strip("/"))
            clean_path = re.sub(r"_+", "_", clean_path)
            base_name = f"{method.lower()}_{clean_path}"

        # Ensure filename is clean
        return re.sub(r"[^a-zA-Z0-9_-]", "_", base_name)

    def _generate_tag_markdown(self, tag: str, endpoints: list[dict[str, Any]]) -> str:
        """Generate markdown documentation for all endpoints in a tag."""
        lines = []

        # API header info
        lines.append(f"API: {self.base_info['title']} v{self.base_info['version']}")
        if self.base_info.get("description"):
            lines.append(f"Description: {self.base_info['description']}")
        lines.append("")

        # Tag info
        tag_info = self._get_tag_info(tag)
        if tag != "Untagged":
            lines.append(f"TAG: {tag}")
            if tag_info and tag_info.get("description"):
                lines.append(f"Description: {tag_info['description']}")
            lines.append("")

        lines.append("ENDPOINTS:")
        lines.append("")

        # Generate documentation for each endpoint
        for endpoint in endpoints:
            method = endpoint["method"]
            path = endpoint["path"]
            operation = endpoint["operation"]

            endpoint_lines = self._generate_endpoint_section_minified(method, path, operation)
            lines.extend(endpoint_lines)
            lines.append("")

        # Collect all schemas used in this tag
        schemas_used = self._collect_schemas_for_tag(endpoints)
        if schemas_used:
            lines.append("SCHEMAS:")
            lines.append("")

            for schema_name in sorted(schemas_used):
                schema_lines = self._format_schema_minified(schema_name)
                lines.extend(schema_lines)
                lines.append("")

        # Add security info if present
        security_schemes = self.schema.get("components", {}).get("securitySchemes", {})
        if security_schemes:
            lines.append("SECURITY:")
            for scheme_name, scheme in security_schemes.items():
                scheme_type = scheme.get("type", "unknown")
                if scheme_type == "apiKey":
                    location = scheme.get("in", "header")
                    key_name = scheme.get("name", "key")
                    lines.append(f"- API Key authentication ({location}: {key_name})")
                elif scheme_type == "http":
                    scheme_name_val = scheme.get("scheme", "bearer")
                    lines.append(f"- HTTP {scheme_name_val} authentication")
                else:
                    lines.append(f"- {scheme_name} ({scheme_type})")
            lines.append("")

        return "\n".join(lines)

    def _get_tag_info(self, tag: str) -> dict[str, Any] | None:
        """Get tag information from schema if available."""
        tags = self.schema.get("tags", [])
        for tag_info in tags:
            if tag_info.get("name") == tag:
                return tag_info
        return None

    def _generate_anchor(self, method: str, path: str) -> str:
        """Generate URL anchor for endpoint."""
        anchor = f"{method.lower()}-{path}"
        anchor = re.sub(r"[^a-zA-Z0-9_-]", "-", anchor)
        anchor = re.sub(r"-+", "-", anchor)
        return anchor.strip("-")

    def _generate_endpoint_section_minified(self, method: str, path: str, operation: dict[str, Any]) -> list[str]:
        """Generate minified markdown section for a single endpoint."""
        lines = []

        # Endpoint header
        lines.append(f"{method.upper()} {path}")

        # Summary
        if operation.get("summary"):
            lines.append(f"  Summary: {operation['summary']}")

        # Description (only if different from summary)
        description = operation.get("description")
        if description and description != operation.get("summary"):
            lines.append(f"  Description: {description}")

        # Parameters
        parameters = operation.get("parameters", [])
        if parameters:
            lines.append("  Parameters:")
            for param in parameters:
                name = param.get("name", "")
                location = param.get("in", "query")
                param_type = self._get_parameter_type_minified(param)
                required = " (required)" if param.get("required", False) else " (optional)"
                description = param.get("description", "No description")
                lines.append(f"    - {name} ({location}, {param_type}{required}): {description}")

        # Request Body
        request_body = operation.get("requestBody")
        if request_body:
            lines.append("  Request Body:")
            content = request_body.get("content", {})
            for media_type, media_content in content.items():
                lines.append(f"    Content: {media_type}")
                schema = media_content.get("schema", {})
                if schema:
                    schema_ref = self._get_schema_reference(schema)
                    lines.append(f"    Schema: {schema_ref}")

        # Responses
        responses = operation.get("responses", {})
        if responses:
            lines.append("  Responses:")
            for status_code, response in responses.items():
                description = response.get("description", "No description")
                lines.append(f"    {status_code}: {description}")

                content = response.get("content", {})
                for media_type, media_content in content.items():
                    lines.append(f"      Content: {media_type}")
                    schema = media_content.get("schema", {})
                    if schema:
                        schema_ref = self._get_schema_reference(schema)
                        lines.append(f"      Schema: {schema_ref}")

        return lines

    def _get_parameter_type(self, param: dict[str, Any]) -> str:
        """Extract and format parameter type information."""
        schema = param.get("schema", {})
        param_type = schema.get("type", "string")

        if schema.get("format"):
            return f"{param_type} ({schema['format']})"
        elif schema.get("enum"):
            return f"enum: {', '.join(map(str, schema['enum']))}"
        else:
            return param_type

    def _get_parameter_type_minified(self, param: dict[str, Any]) -> str:
        """Extract simplified parameter type information."""
        schema = param.get("schema", {})
        return schema.get("type", "string")

    def _get_schema_reference(self, schema: dict[str, Any]) -> str:
        """Get schema reference name or type."""
        if "$ref" in schema:
            return schema["$ref"].split("/")[-1]
        elif schema.get("type") == "array" and "items" in schema:
            items = schema["items"]
            if "$ref" in items:
                return f"array of {items['$ref'].split('/')[-1]}"
            else:
                return f"array of {items.get('type', 'unknown')}"
        else:
            return schema.get("type", "Unknown")

    def _collect_schemas_for_tag(self, endpoints: list[dict[str, Any]]) -> set[str]:
        """Collect all schema references used in endpoints of this tag."""
        schemas = set()

        for endpoint in endpoints:
            operation = endpoint["operation"]

            # Check request body schemas
            request_body = operation.get("requestBody", {})
            content = request_body.get("content", {})
            for media_content in content.values():
                schema = media_content.get("schema", {})
                self._extract_schema_refs(schema, schemas)

            # Check response schemas
            responses = operation.get("responses", {})
            for response in responses.values():
                content = response.get("content", {})
                for media_content in content.values():
                    schema = media_content.get("schema", {})
                    self._extract_schema_refs(schema, schemas)

        return schemas

    def _extract_schema_refs(self, schema: dict[str, Any], refs: set[str]):
        """Recursively extract schema references."""
        if "$ref" in schema:
            ref_name = schema["$ref"].split("/")[-1]
            refs.add(ref_name)
        elif schema.get("type") == "array" and "items" in schema:
            self._extract_schema_refs(schema["items"], refs)
        elif schema.get("type") == "object" and "properties" in schema:
            for prop_schema in schema["properties"].values():
                self._extract_schema_refs(prop_schema, refs)

    def _format_schema_minified(self, schema_name: str) -> list[str]:
        """Format a schema definition in minified style."""
        lines = []
        schema = self.schemas.get(schema_name)

        if not schema:
            lines.append(f"{schema_name}: (not found)")
            return lines

        lines.append(f"{schema_name}:")

        if schema.get("type") == "object":
            properties = schema.get("properties", {})
            required = schema.get("required", [])

            for prop_name, prop_schema in properties.items():
                prop_type = self._get_schema_reference(prop_schema)
                required_marker = " (required)" if prop_name in required else ""
                lines.append(f"  - {prop_name}: {prop_type}{required_marker}")

        elif schema.get("type") == "array":
            items = schema.get("items", {})
            item_type = self._get_schema_reference(items)
            lines.append(f"  - array of {item_type}")

        elif schema.get("enum"):
            enum_values = ", ".join(map(str, schema["enum"]))
            lines.append(f"  - enum: [{enum_values}]")

        else:
            schema_type = schema.get("type", "unknown")
            lines.append(f"  - type: {schema_type}")

        return lines

    def _resolve_ref(self, ref: str) -> dict[str, Any]:
        """Resolve a $ref to the actual schema definition."""
        if ref.startswith("#/components/schemas/"):
            schema_name = ref.split("/")[-1]
            return self.schemas.get(schema_name, {})
        elif ref.startswith("#/"):
            # Handle other internal references
            parts = ref[2:].split("/")
            result = self.schema
            for part in parts:
                result = result.get(part, {})
            return result
        else:
            # External references not supported
            return {}

    def _get_type_info(self, schema: dict[str, Any]) -> str:
        """Get comprehensive type information including constraints."""
        if "$ref" in schema:
            resolved = self._resolve_ref(schema["$ref"])
            if resolved:
                schema_name = schema["$ref"].split("/")[-1]
                return f"{schema_name} (ref)"
            return "unknown (ref)"

        schema_type = schema.get("type", "unknown")
        type_info = [schema_type]

        # Add format
        if schema.get("format"):
            type_info.append(f"format: {schema['format']}")

        # Add constraints
        constraints = []
        if "minimum" in schema:
            constraints.append(f"min: {schema['minimum']}")
        if "maximum" in schema:
            constraints.append(f"max: {schema['maximum']}")
        if "minLength" in schema:
            constraints.append(f"minLen: {schema['minLength']}")
        if "maxLength" in schema:
            constraints.append(f"maxLen: {schema['maxLength']}")
        if "pattern" in schema:
            constraints.append(f"pattern: {schema['pattern']}")
        if schema.get("enum"):
            enum_values = ", ".join([str(v) for v in schema["enum"][:3]])
            if len(schema["enum"]) > 3:
                enum_values += "..."
            constraints.append(f"enum: [{enum_values}]")

        if constraints:
            type_info.append(f"({', '.join(constraints)})")

        return " ".join(type_info)

    def _format_schema_detailed(
        self, schema: dict[str, Any], indent: int = 0, visited_refs: set | None = None
    ) -> list[str]:
        """Format a JSON schema into readable markdown with full type definitions."""
        if visited_refs is None:
            visited_refs = set()

        lines = []
        prefix = "  " * indent

        # Handle $ref
        if "$ref" in schema:
            ref = schema["$ref"]
            if ref in visited_refs:
                # Prevent infinite recursion
                lines.append(f"{prefix}**Type:** {ref.split('/')[-1]} (circular reference)")
                return lines

            visited_refs.add(ref)
            resolved_schema = self._resolve_ref(ref)
            if resolved_schema:
                schema_name = ref.split("/")[-1]
                lines.append(f"{prefix}**Schema:** `{schema_name}`")
                lines.append("")
                nested_lines = self._format_schema(resolved_schema, indent, visited_refs.copy())
                lines.extend(nested_lines)
            else:
                lines.append(f"{prefix}**Type:** {ref} (unresolved reference)")
            return lines

        schema_type = schema.get("type")

        # Add type and constraints info
        type_info = self._get_type_info(schema)
        lines.append(f"{prefix}**Type:** `{type_info}`")

        # Add description
        if schema.get("description"):
            lines.append(f"{prefix}**Description:** {schema['description']}")

        # Add default value
        if "default" in schema:
            lines.append(f"{prefix}**Default:** `{json.dumps(schema['default'])}`")

        # Add example
        if "example" in schema:
            lines.append(f"{prefix}**Example:** `{json.dumps(schema['example'])}`")

        lines.append("")

        # Handle object properties
        if schema_type == "object":
            properties = schema.get("properties", {})
            required = schema.get("required", [])
            additional_props = schema.get("additionalProperties")

            if properties:
                lines.append(f"{prefix}**Properties:**")
                lines.append("")

                # Create table for properties
                lines.append(f"{prefix}| Property | Type | Required | Description |")
                lines.append(f"{prefix}|----------|------|----------|-------------|")

                for prop_name, prop_schema in properties.items():
                    is_required = prop_name in required
                    req_marker = "✓" if is_required else ""
                    prop_type = self._get_type_info(prop_schema)
                    description = prop_schema.get("description", "").replace("\n", " ")[:100]
                    if len(prop_schema.get("description", "")) > 100:
                        description += "..."

                    lines.append(f"{prefix}| `{prop_name}` | {prop_type} | {req_marker} | {description} |")

                lines.append("")

                # Detailed property schemas for complex types
                for prop_name, prop_schema in properties.items():
                    if prop_schema.get("type") in ["object", "array"] or "$ref" in prop_schema:
                        lines.append(f"{prefix}#### `{prop_name}` Details")
                        lines.append("")
                        nested_lines = self._format_schema(prop_schema, indent + 1, visited_refs.copy())
                        lines.extend(nested_lines)

            if additional_props is not None:
                if additional_props is True:
                    lines.append(f"{prefix}**Additional Properties:** Allowed")
                elif additional_props is False:
                    lines.append(f"{prefix}**Additional Properties:** Not allowed")
                else:
                    lines.append(f"{prefix}**Additional Properties:**")
                    lines.append("")
                    nested_lines = self._format_schema(additional_props, indent + 1, visited_refs.copy())
                    lines.extend(nested_lines)
                lines.append("")

        # Handle array items
        elif schema_type == "array":
            items = schema.get("items", {})
            if items:
                lines.append(f"{prefix}**Array Items:**")
                lines.append("")
                nested_lines = self._format_schema(items, indent + 1, visited_refs.copy())
                lines.extend(nested_lines)

            # Array constraints
            if "minItems" in schema:
                lines.append(f"{prefix}**Minimum Items:** {schema['minItems']}")
            if "maxItems" in schema:
                lines.append(f"{prefix}**Maximum Items:** {schema['maxItems']}")
            if schema.get("uniqueItems"):
                lines.append(f"{prefix}**Unique Items:** Yes")

            lines.append("")

        # Handle oneOf, anyOf, allOf
        for keyword in ["oneOf", "anyOf", "allOf"]:
            if keyword in schema:
                lines.append(f"{prefix}**{keyword.title()}:**")
                lines.append("")
                for i, sub_schema in enumerate(schema[keyword]):
                    lines.append(f"{prefix}**Option {i + 1}:**")
                    nested_lines = self._format_schema(sub_schema, indent + 1, visited_refs.copy())
                    lines.extend(nested_lines)
                lines.append("")

        return lines


def convert_openapi_to_markdown(
    schema_path: str | dict[str, Any] | Path, output_dir: str | Path = "api_docs"
) -> list[str]:
    """
    Convenience function to convert OpenAPI schema to markdown files.

    Args:
        schema_path: Path to OpenAPI schema file, schema dict, or schema string
        output_dir: Directory to save markdown files

    Returns:
        List of generated file paths
    """
    converter = OpenAPIToMarkdownConverter(schema_path)
    return converter.convert_to_markdown_files(output_dir)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Convert OpenAPI schema to markdown documentation")
    parser.add_argument("schema", help="Path to OpenAPI schema file (JSON or YAML)")
    parser.add_argument("-o", "--output", default="api_docs", help="Output directory for markdown files")

    args = parser.parse_args()

    try:
        generated_files = convert_openapi_to_markdown(args.schema, args.output)
        print(f"Generated {len(generated_files)} markdown files in '{args.output}':")
        for file_path in generated_files:
            print(f"  - {file_path}")
    except Exception as e:
        print(f"Error: {e}")
        exit(1)
