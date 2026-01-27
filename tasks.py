import platform
import textwrap
import time
from pathlib import Path

import httpx
from invoke import Context, Exit, call, task
from packaging.version import Version
from termcolor import cprint

MIN_NODE_VERSION = "18"


@task(help={"command": "Docker command to run: 'up' to start services, 'down' to stop services"})
def docker(c: Context, command):
    """Manage Docker services (PostgreSQL and Redis)."""
    if command == "up":
        c.run("docker compose -f docker-compose-dev.yml up -d")
    elif command == "down":
        c.run("docker compose -f docker-compose-dev.yml down")
    else:
        raise Exit(f"Unknown docker command: {command}", -1)


@task(pre=[call(docker, command="up")])
def up(c: Context):
    """Start PostgreSQL and Redis services using Docker."""
    pass


@task(pre=[call(docker, command="down")])
def down(c: Context):
    """Stop PostgreSQL and Redis services."""
    pass


@task(
    help={
        "upgrade_all": "Upgrade all packages to latest versions",
        "upgrade_package": "Upgrade specific package (e.g. --upgrade-package django)",
    }
)
def requirements(c: Context, upgrade_all=False, upgrade_package=None):
    """Update Python dependencies using uv lock and optionally sync environment."""
    if upgrade_all and upgrade_package:
        raise Exit("Cannot specify both upgrade and upgrade-package", -1)
    has_uv = c.run("uv -V", hide=True, timeout=1, warn=True)
    if not has_uv.ok:
        cprint("uv is not installed. See https://docs.astral.sh/uv/getting-started/installation/", "red")
        return 1

    cmd = "uv lock"
    if upgrade_all:
        cmd += " --upgrade"
    elif upgrade_package:
        cmd += f" --upgrade-package {upgrade_package}"

    c.run(cmd)

    result = c.run("uv sync --frozen --dev --dry-run", echo=True, pty=True)
    if "no changes" in result.stdout:
        return None

    if _confirm("\nDo you want to sync your venv with the new requirements?", _exit=False):
        c.run("uv sync --frozen --dev", echo=True, pty=True)
        return None
    return None


@task
def translations(c: Context):
    """Extract and compile Django translation messages for all languages."""
    c.run("python manage.py makemessages --all --ignore node_modules --ignore venv")
    c.run("python manage.py makemessages -d djangojs --all --ignore node_modules --ignore venv")
    c.run("python manage.py compilemessages")


@task
def schema(c: Context):
    """Generate OpenAPI schema file for the API."""
    c.run("python manage.py spectacular --file api-schema.yml --validate")


@task(help={"step": "Run setup interactively, confirming each step"})
def setup_dev_env(c: Context, step=False):
    """Set up complete development environment: Docker, migrations, assets, pre-commit hooks."""
    cprint("Setting up dev environment", "green")
    if not step and not _confirm(
        textwrap.dedent(
            """
    This will start docker, run DB migrations, build JS & CSS resources, and install pre-commit hooks.
    Do you want to continue?
    """
        ),
        _exit=False,
    ):
        cprint("You can also run this one step at a time with the '-s' or '--step' flag", "yellow")
        raise Exit(None, -1)

    cprint("\nStarting docker", "green")
    if not step or _confirm("\tOK?", _exit=False):
        docker(c, command="up")

    _run_with_confirm(c, "Install pre-commit hooks", "pre-commit install --install-hooks", step)

    if not Path(".env").exists():
        cprint("\nCreating .env file", "green")
        _run_with_confirm(c, "Create .env file", "cp .env.example .env", step)
    else:
        cprint("\nSkipping .env file creation, file already exists", "yellow")

    _run_with_confirm(c, "Run DB migrations", "python manage.py migrate", step)

    cprint(f"\nChecking node version (>{MIN_NODE_VERSION} required)", "green")
    if not _check_node_version(c):
        cprint(f"Node version should be {MIN_NODE_VERSION} or higher", "red")
        cprint("\nSkipping front end build. Run 'inv npm --install' once you have upgraded node.", "yellow")
    else:
        cprint("\nInstalling npm packages and building front end resources", "green")
        if not step or _confirm("\tOK?", _exit=False):
            npm(c, install=True)

    _run_with_confirm(c, "Create superuser", "python manage.py createsuperuser", step)


def _run_with_confirm(c: Context, message, command, step=False):
    cprint(f"\n{message}", "green")
    if not step or _confirm("\tOK?", _exit=False):
        c.run(command, echo=True, pty=True)
        return True


def _check_node_version(c: Context):
    res = c.run("node -v", echo=True)
    version = res.stdout.strip()
    if version.startswith("v"):
        version = version[1:]
    ver = Version(version)
    return ver >= Version(MIN_NODE_VERSION)


@task
def ngrok_url(c: Context):
    """Start ngrok tunnel for local development and return public URL."""
    #  You need to have ngrok installed on your system
    c.run("ngrok http 8000", echo=True, asynchronous=True)
    while True:
        try:
            response = httpx.get("http://localhost:4040/api/tunnels", timeout=10)
            if response.status_code == 200:
                public_url = response.json()["tunnels"][0]["public_url"].split("https://")[1]
                break
        except Exception:
            time.sleep(1)
            print("Trying to a public address from ngrok")

    print(f"Public address found: {public_url}")
    return public_url


@task(aliases=["django"], help={"public": "Expose server publicly via ngrok tunnel"})
def runserver(c: Context, public=False):
    """Start Django development server (alias: inv django)."""
    runserver_command = "python manage.py runserver"
    if public:
        public_url = ngrok_url(c)
        if platform.system() == "Windows":
            runserver_command = f"powershell -Command \"$env:SITE_URL_ROOT='{public_url}'; {runserver_command}\""
            pty = False
        else:
            runserver_command = f"SITE_URL_ROOT={public_url} {runserver_command}"
            pty = True
    else:
        pty = True

    c.run(runserver_command, echo=True, pty=pty)


@task(
    help={
        "gevent": "Use gevent pool for async tasks (disables beat scheduler)",
        "beat": "Include beat scheduler for periodic tasks (default: True)",
    }
)
def celery(c: Context, gevent=False, beat=True):
    """Start Celery worker with auto-reload on code changes."""
    cmd = "celery -A config worker -l INFO"
    if gevent:
        cmd += " --pool gevent --concurrency 10"
    else:
        cmd += " --pool=solo"
        if beat:
            cmd += " -B"

    if gevent:
        cprint("Starting celery worker with gevent pool. This will not run celery beat.", "yellow")
    c.run(f'watchfiles --filter python "{cmd}"', echo=True, pty=True)


@task(
    help={
        "no_fix": "Only check for issues, don't auto-fix",
        "unsafe_fixes": "Apply potentially unsafe automatic fixes",
        "paths": "Specific files or directories to check (space-separated)",
    }
)
def ruff(c: Context, no_fix=False, unsafe_fixes=False, paths=""):
    """Run ruff checks and formatting. Use --unsafe-fixes to apply unsafe fixes."""
    fix_flag = "" if no_fix else "--fix"
    unsafe_fixes_flag = "--unsafe-fixes" if unsafe_fixes else ""
    target_paths = paths if paths else "."
    c.run(f"ruff check {fix_flag} {unsafe_fixes_flag} {target_paths}", echo=True, pty=True)
    c.run(f"ruff format {target_paths}", echo=True, pty=True)


@task(
    help={
        "watch": "Build assets and watch for changes (npm run dev-watch)",
        "install": "Install npm packages before building",
    }
)
def npm(c: Context, watch=False, install=False):
    """Build frontend assets with webpack. Use --watch for development."""
    if install:
        c.run("npm install", echo=True)
    cmd = "dev-watch" if watch else "dev"
    c.run(f"npm run {cmd}", echo=True, pty=True)


def _confirm(message, _exit=True, exit_message="Done"):
    response = input(f"{message} (y/n): ")
    confirmed = response.lower() == "y"
    if not confirmed and _exit:
        raise Exit(exit_message, -1)
    return confirmed
