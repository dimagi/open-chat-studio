import datetime
import inspect
import json
import time
from typing import Any

from pydantic import BaseModel, Field, field_validator
from pydantic_core import PydanticCustomError
from pydantic_core.core_schema import FieldValidationInfo
from RestrictedPython import compile_restricted, limited_builtins, safe_builtins, utility_builtins
from RestrictedPython.Eval import default_guarded_getitem, default_guarded_getiter
from RestrictedPython.Guards import guarded_iter_unpack_sequence


class RestrictedPythonExecutionMixin(BaseModel):
    """Mixin for executing Python code safely using RestrictedPython."""

    code: str = Field(description="The code to run")

    @classmethod
    def _get_function_args(cls):
        """The required arguments for the 'main' function"""

        raise NotImplementedError()

    @field_validator("code")
    def validate_code(cls, value, info: FieldValidationInfo):
        if not value:
            value = cls._get_default_code()
        try:
            byte_code = compile_restricted(
                value,
                filename="<inline code>",
                mode="exec",
            )
            custom_locals = {}
            try:
                exec(byte_code, {}, custom_locals)
            except Exception as exc:
                raise PydanticCustomError("invalid_code", "{error}", {"error": str(exc)}) from exc

            try:
                main = custom_locals["main"]
            except KeyError:
                raise SyntaxError("You must define a 'main' function") from None

            for name, item in custom_locals.items():
                if name != "main" and inspect.isfunction(item):
                    raise SyntaxError(
                        "You can only define a single function, 'main' at the top level. "
                        "You may use nested functions inside that function if required"
                    )

            cls._validate_function_signature(main)

        except SyntaxError as exc:
            raise PydanticCustomError("invalid_code", "{error}", {"error": exc.msg}) from None
        return value

    @classmethod
    def _get_default_code(cls) -> str:
        """Override this method in subclasses to provide default code."""
        raise NotImplementedError("Subclasses must implement _get_default_code")

    @classmethod
    def _validate_function_signature(cls, main_function):
        """Validate that the main function has the expected signature."""
        expected_args = cls._get_function_args()
        sig = inspect.signature(main_function)
        params = list(sig.parameters.values())

        expected_signature = f"main({', '.join(expected_args)})"

        if len(params) != len(expected_args):
            raise SyntaxError(f"The main function should have the signature {expected_signature} only.")

        for i, (param, expected_arg) in enumerate(zip(params, expected_args, strict=False)):
            if expected_arg.startswith("**"):
                # VAR_KEYWORD parameter (**kwargs)
                expected_name = expected_arg[2:]  # Remove **
                if param.kind != inspect.Parameter.VAR_KEYWORD or param.name != expected_name:
                    raise SyntaxError(f"Parameter {i + 1} should be **{expected_name}")
            elif expected_arg.startswith("*"):
                # VAR_POSITIONAL parameter (*args)
                expected_name = expected_arg[1:]  # Remove *
                if param.kind != inspect.Parameter.VAR_POSITIONAL or param.name != expected_name:
                    raise SyntaxError(f"Parameter {i + 1} should be *{expected_name}")
            else:
                # Regular parameter
                if param.name != expected_arg or param.kind not in [
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                ]:
                    raise SyntaxError(f"Parameter {i + 1} should be '{expected_arg}'")

    @classmethod
    def _get_custom_globals(cls) -> dict[str, Any]:
        """Get the base global environment for code execution."""
        custom_globals = {
            "__builtins__": cls._get_custom_builtins(),
            "json": json,
            "datetime": datetime,
            "time": time,
            "_getitem_": default_guarded_getitem,
            "_getiter_": default_guarded_getiter,
            "_iter_unpack_sequence_": guarded_iter_unpack_sequence,
            "_write_": lambda x: x,
        }
        return custom_globals

    @classmethod
    def _get_custom_builtins(cls) -> dict[str, Any]:
        """Get the base builtins for code execution."""
        allowed_modules = {
            "json",
            "re",
            "datetime",
            "time",
            "random",
        }
        custom_builtins = safe_builtins.copy()
        custom_builtins.update(
            {
                "min": min,
                "max": max,
                "sum": sum,
                "abs": abs,
                "all": all,
                "any": any,
                "datetime": datetime,
            }
        )

        custom_builtins.update(utility_builtins)
        custom_builtins.update(limited_builtins)

        def guarded_import(name, *args, **kwargs):
            if name not in allowed_modules:
                raise ImportError(f"Importing '{name}' is not allowed")
            return __import__(name, *args, **kwargs)

        custom_builtins["__import__"] = guarded_import
        return custom_builtins

    def compile_and_execute_code(
        self,
        custom_globals: dict[str, Any] = None,
        *args,
        **kwargs,
    ) -> Any:
        """
        Compile and execute Python code safely.
        Args:
            custom_globals: Additional globals to include
            *args, **kwargs: Arguments to pass to the function
        Returns:
            The result of calling the function
        """
        function_name = "main"
        filename = "<inline_code>"
        byte_code = compile_restricted(
            self.code,
            filename=filename,
            mode="exec",
        )
        custom_locals = {}
        exec(byte_code, custom_globals, custom_locals)

        # Call the main function
        if function_name not in custom_locals:
            raise ValueError(f"Function {function_name} not found in code")

        return custom_locals[function_name](*args, **kwargs)
