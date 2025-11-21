"""Swagger/OpenAPI specification parser for Bank API analysis."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


class SwaggerParser:
    """Parse and extract information from Swagger/OpenAPI specifications."""

    def __init__(self, spec_path: str):
        """Initialize parser with Swagger spec file.

        Args:
            spec_path: Path to Swagger JSON file

        Raises:
            FileNotFoundError: If spec file doesn't exist
            json.JSONDecodeError: If spec is not valid JSON
        """
        self.spec_path = Path(spec_path)
        if not self.spec_path.exists():
            raise FileNotFoundError(f"Swagger spec not found: {spec_path}")

        with open(self.spec_path, "r", encoding="utf-8") as f:
            self.spec = json.load(f)

        self._validate_spec()

    def _validate_spec(self) -> None:
        """Validate that spec has required Swagger structure."""
        if "paths" not in self.spec:
            raise ValueError("Swagger spec missing 'paths' section")
        if "components" not in self.spec or "schemas" not in self.spec["components"]:
            raise ValueError("Swagger spec missing 'components.schemas' section")

    def get_ais_endpoints(self) -> List[Dict[str, Any]]:
        """Extract AIS (Account Information Service) endpoints.

        AIS endpoints are those related to accounts, balances, and transactions.

        Returns:
            List of endpoint dictionaries with path, method, and details
        """
        ais_keywords = {"/accounts", "/balances", "/transactions"}
        endpoints = []

        for path, path_item in self.spec.get("paths", {}).items():
            # Check if this is an AIS endpoint
            if not any(keyword in path for keyword in ais_keywords):
                continue

            for method, operation in path_item.items():
                if method.startswith("x-"):  # Skip vendor extensions
                    continue
                if method not in {"get", "post", "put", "delete", "patch"}:
                    continue

                endpoint = {
                    "path": path,
                    "method": method.upper(),
                    "operationId": operation.get("operationId", ""),
                    "summary": operation.get("summary", ""),
                    "description": operation.get("description", ""),
                    "parameters": operation.get("parameters", []),
                    "requestBody": operation.get("requestBody"),
                    "responses": operation.get("responses", {}),
                }
                endpoints.append(endpoint)

        return endpoints

    def get_all_endpoints(self) -> List[Dict[str, Any]]:
        """Extract all endpoints from the spec.

        Returns:
            List of all endpoint dictionaries
        """
        endpoints = []

        for path, path_item in self.spec.get("paths", {}).items():
            for method, operation in path_item.items():
                if method.startswith("x-"):
                    continue
                if method not in {"get", "post", "put", "delete", "patch"}:
                    continue

                endpoint = {
                    "path": path,
                    "method": method.upper(),
                    "operationId": operation.get("operationId", ""),
                    "summary": operation.get("summary", ""),
                    "description": operation.get("description", ""),
                    "parameters": operation.get("parameters", []),
                    "requestBody": operation.get("requestBody"),
                    "responses": operation.get("responses", {}),
                }
                endpoints.append(endpoint)

        return endpoints

    def get_response_schema(self, endpoint: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Get the response schema for an endpoint.

        Args:
            endpoint: Endpoint dictionary from get_ais_endpoints()

        Returns:
            Response schema dict or None if not found
        """
        responses = endpoint.get("responses", {})

        # Try 200, 201, then first successful response
        for status in ["200", "201"]:
            if status in responses:
                return self._extract_schema_from_response(responses[status])

        for status, response in responses.items():
            if status.startswith("2"):
                return self._extract_schema_from_response(response)

        return None

    def _extract_schema_from_response(self, response: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract schema from a response object.

        Args:
            response: Response object from Swagger spec

        Returns:
            Schema dict or None
        """
        content = response.get("content", {})
        if "application/json" in content:
            return content["application/json"].get("schema")
        return None

    def get_response_fields(self, endpoint: Dict[str, Any]) -> Dict[str, Any]:
        """Get all fields in the response schema for an endpoint.

        Flattens nested objects and resolves $ref references.

        Args:
            endpoint: Endpoint dictionary

        Returns:
            Dictionary mapping field names to their types and details
        """
        schema = self.get_response_schema(endpoint)
        if not schema:
            return {}

        return self._flatten_schema(schema)

    def _flatten_schema(
        self, schema: Dict[str, Any], prefix: str = "", visited: Optional[Set[str]] = None
    ) -> Dict[str, Any]:
        """Recursively flatten a schema into field definitions.

        Args:
            schema: Schema object to flatten
            prefix: Prefix for nested fields (e.g., "data.accounts")
            visited: Set of already visited schema refs (to avoid cycles)

        Returns:
            Dictionary of flattened fields
        """
        if visited is None:
            visited = set()

        fields = {}

        # Handle $ref
        if "$ref" in schema:
            ref = schema["$ref"]
            if ref in visited:
                return fields
            visited.add(ref)
            schema = self._resolve_ref(ref)

        # Handle arrays
        if schema.get("type") == "array":
            items_schema = schema.get("items", {})
            array_fields = self._flatten_schema(items_schema, prefix, visited)
            for field_name, field_info in array_fields.items():
                fields[f"{prefix}[]{field_name}" if prefix else f"[]{field_name}"] = field_info
            return fields

        # Handle objects
        if schema.get("type") == "object":
            properties = schema.get("properties", {})
            required = schema.get("required", [])

            for prop_name, prop_schema in properties.items():
                field_name = f"{prefix}.{prop_name}" if prefix else prop_name
                field_info = {
                    "type": prop_schema.get("type", "unknown"),
                    "required": prop_name in required,
                    "description": prop_schema.get("description", ""),
                }

                # Add format if present
                if "format" in prop_schema:
                    field_info["format"] = prop_schema["format"]

                # Add enum if present
                if "enum" in prop_schema:
                    field_info["enum"] = prop_schema["enum"]

                fields[field_name] = field_info

                # Recursively flatten nested objects
                if prop_schema.get("type") == "object" or "$ref" in prop_schema:
                    nested_fields = self._flatten_schema(prop_schema, field_name, visited)
                    fields.update(nested_fields)
                elif prop_schema.get("type") == "array":
                    nested_fields = self._flatten_schema(prop_schema, field_name, visited)
                    fields.update(nested_fields)

        return fields

    def _resolve_ref(self, ref: str) -> Dict[str, Any]:
        """Resolve a $ref reference to its schema.

        Args:
            ref: Reference string (e.g., "#/components/schemas/Account")

        Returns:
            Resolved schema dictionary
        """
        if not ref.startswith("#/"):
            return {}

        parts = ref[2:].split("/")
        current = self.spec

        for part in parts:
            if isinstance(current, dict):
                current = current.get(part, {})
            else:
                return {}

        return current if isinstance(current, dict) else {}

    def get_endpoint_by_operation_id(self, operation_id: str) -> Optional[Dict[str, Any]]:
        """Find an endpoint by its operationId.

        Args:
            operation_id: The operationId to search for

        Returns:
            Endpoint dictionary or None if not found
        """
        for endpoint in self.get_all_endpoints():
            if endpoint.get("operationId") == operation_id:
                return endpoint
        return None

    def get_schema_by_name(self, schema_name: str) -> Optional[Dict[str, Any]]:
        """Get a schema definition by name.

        Args:
            schema_name: Name of the schema (e.g., "Account")

        Returns:
            Schema dictionary or None if not found
        """
        schemas = self.spec.get("components", {}).get("schemas", {})
        return schemas.get(schema_name)

    def get_all_schemas(self) -> Dict[str, Dict[str, Any]]:
        """Get all schema definitions.

        Returns:
            Dictionary mapping schema names to their definitions
        """
        return self.spec.get("components", {}).get("schemas", {})
