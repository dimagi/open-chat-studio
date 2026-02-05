"""Tests for schema_utils module, including operation extraction and parameter parsing."""

import json
from pathlib import Path

from apps.custom_actions.schema_utils import (
    APIOperationDetails,
    ParameterDetail,
    get_operations_from_spec_dict,
)


def load_test_data(filename: str) -> dict:
    """Load test data from the data directory."""
    data_path = Path(__file__).parent / "data" / filename
    with open(data_path) as f:
        return json.load(f)


class TestGetOperationsFromSpecDict:
    """Test extraction of operations from OpenAPI spec with varying endpoint types and parameters."""

    def _get_operation_by_id(self, operations: list[APIOperationDetails], op_id: str) -> APIOperationDetails | None:
        """Helper to find operation by ID."""
        return next((op for op in operations if op.operation_id == op_id), None)

    def _get_parameter(self, operation: APIOperationDetails, param_name: str) -> ParameterDetail | None:
        """Helper to find parameter by name within an operation."""
        return next((p for p in operation.parameters if p.name == param_name), None)

    def _assert_parameter(
        self,
        operation: APIOperationDetails,
        param_name: str,
        schema_type: str,
        required: bool = False,
        default: any = None,
    ):
        """Helper to assert parameter exists with expected properties."""
        param = self._get_parameter(operation, param_name)
        assert param is not None, f"Parameter '{param_name}' not found in operation"
        assert param.schema_type == schema_type, f"Expected type {schema_type}, got {param.schema_type}"
        assert param.required == required, f"Expected required={required}, got {param.required}"
        if default is not None:
            assert param.default == default, f"Expected default={default}, got {param.default}"

    def test_extract_operations_with_varying_types(self):
        """
        Test that all endpoints with varying HTTP methods, query params, path params,
        and request body types are correctly extracted.
        """
        spec = load_test_data("users_api_spec.json")
        operations = get_operations_from_spec_dict(spec)

        # Verify correct number of operations
        assert len(operations) == 5

        # Verify operation structure
        for op in operations:
            assert isinstance(op, APIOperationDetails)
            assert op.operation_id
            assert op.description
            assert op.path
            assert op.method
            assert isinstance(op.parameters, list)
            for param in op.parameters:
                assert isinstance(param, ParameterDetail)
                assert param.name
                assert param.schema_type

        # Verify all operation_ids and HTTP methods are correct
        operation_ids = {op.operation_id for op in operations}
        assert operation_ids == {"listUsers", "createUser", "getUser", "updateUser", "deleteUser"}
        assert {op.method for op in operations} == {"get", "post", "put", "delete"}

        # Test GET /users - listUsers
        self._test_list_users(operations)

        # Test POST /users - createUser
        self._test_create_user(operations)

        # Test GET /users/{user_id} - getUser
        self._test_get_user(operations)

        # Test PUT /users/{user_id} - updateUser
        self._test_update_user(operations)

        # Test DELETE /users/{user_id} - deleteUser
        self._test_delete_user(operations)

        # Verify all required DataType enum values are covered
        self._verify_all_data_types_covered(operations)

    def _verify_all_data_types_covered(self, operations: list[APIOperationDetails]):
        """Verify that the test spec includes all DataType enum values."""
        # Collect all parameter types from all operations
        all_param_types = set()
        for op in operations:
            for param in op.parameters:
                all_param_types.add(param.schema_type)

        # Verify all required DataType enum values are covered
        # DataType enum values: null, string, number, integer, boolean, array, object
        required_types = {"string", "number", "integer", "boolean", "array", "object"}
        assert required_types.issubset(all_param_types), (
            f"Missing data types. Found: {all_param_types}, Expected to include: {required_types}"
        )

    def _test_list_users(self, operations: list[APIOperationDetails]):
        """Test GET /users operation."""
        list_users = self._get_operation_by_id(operations, "listUsers")
        assert list_users is not None
        assert list_users.method == "get"
        assert list_users.path == "/users"

        # Verify parameters
        param_names = {p.name for p in list_users.parameters}
        assert param_names == {"limit", "offset", "active", "name"}

        self._assert_parameter(
            operation=list_users, param_name="limit", schema_type="integer", required=False, default=10
        )
        self._assert_parameter(operation=list_users, param_name="offset", schema_type="integer", required=False)
        self._assert_parameter(operation=list_users, param_name="active", schema_type="boolean", required=False)
        self._assert_parameter(operation=list_users, param_name="name", schema_type="string", required=False)

    def _test_create_user(self, operations: list[APIOperationDetails]):
        """Test POST /users operation."""
        create_user = self._get_operation_by_id(operations, "createUser")
        assert create_user is not None
        assert create_user.method == "post"
        assert create_user.path == "/users"

        # Verify both query and request body parameters
        param_names = {p.name for p in create_user.parameters}
        assert param_names == {"score", "username", "email", "age", "is_admin", "tags", "rating", "metadata", "notes"}

        # Query parameter with number type
        self._assert_parameter(operation=create_user, param_name="score", schema_type="number", required=False)

        # Request body parameters with various types
        self._assert_parameter(operation=create_user, param_name="username", schema_type="string", required=True)
        self._assert_parameter(operation=create_user, param_name="email", schema_type="string", required=True)
        self._assert_parameter(operation=create_user, param_name="age", schema_type="integer", required=False)
        self._assert_parameter(operation=create_user, param_name="is_admin", schema_type="boolean", required=False)
        self._assert_parameter(operation=create_user, param_name="tags", schema_type="array", required=False)
        self._assert_parameter(operation=create_user, param_name="rating", schema_type="number", required=False)
        self._assert_parameter(operation=create_user, param_name="metadata", schema_type="object", required=False)
        self._assert_parameter(operation=create_user, param_name="notes", schema_type="string", required=False)

    def _test_get_user(self, operations: list[APIOperationDetails]):
        """Test GET /users/{user_id} operation."""
        get_user = self._get_operation_by_id(operations, "getUser")
        assert get_user is not None
        assert get_user.method == "get"
        assert get_user.path == "/users/{user_id}"

        # Verify parameters including path param
        param_names = {p.name for p in get_user.parameters}
        assert param_names == {"user_id", "include_profile"}

        self._assert_parameter(operation=get_user, param_name="user_id", schema_type="string", required=True)
        self._assert_parameter(operation=get_user, param_name="include_profile", schema_type="boolean", required=False)

    def _test_update_user(self, operations: list[APIOperationDetails]):
        """Test PUT /users/{user_id} operation."""
        update_user = self._get_operation_by_id(operations, "updateUser")
        assert update_user is not None
        assert update_user.method == "put"
        assert update_user.path == "/users/{user_id}"

        # Verify both path and request body parameters
        param_names = {p.name for p in update_user.parameters}
        assert param_names == {"user_id", "email", "age", "profile_updated"}

        self._assert_parameter(operation=update_user, param_name="user_id", schema_type="string", required=True)
        self._assert_parameter(operation=update_user, param_name="email", schema_type="string", required=False)
        self._assert_parameter(operation=update_user, param_name="age", schema_type="integer", required=False)
        self._assert_parameter(
            operation=update_user, param_name="profile_updated", schema_type="boolean", required=False
        )

    def _test_delete_user(self, operations: list[APIOperationDetails]):
        """Test DELETE /users/{user_id} operation."""
        delete_user = self._get_operation_by_id(operations, "deleteUser")
        assert delete_user is not None
        assert delete_user.method == "delete"
        assert delete_user.path == "/users/{user_id}"

        # Verify only path parameter
        param_names = {p.name for p in delete_user.parameters}
        assert param_names == {"user_id"}

        self._assert_parameter(operation=delete_user, param_name="user_id", schema_type="string", required=True)
