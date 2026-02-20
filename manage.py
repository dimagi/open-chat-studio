#!/usr/bin/env python
import os
import sys

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    port = os.environ.get("PORT")
    if port:
        # If the runserver command is used, insert the port into the arguments
        if "runserver" in sys.argv:
            # Remove any existing port argument first
            sys.argv = [arg for arg in sys.argv if not arg.isdigit()]
            sys.argv.append(port)
    execute_from_command_line(sys.argv)
