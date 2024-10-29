import textwrap
import time
from distutils.version import LooseVersion
from pathlib import Path

import requests
from invoke import Context, Exit, call, task
from termcolor import cprint

MIN_NODE_VERSION = "18"


@task
def docker(c: Context, command):
    if command == "up":
        c.run("docker compose -f docker-compose-dev.yml up -d")
    elif command == "down":
        c.run("docker compose -f docker-compose-dev.yml down")
    else:
        raise Exit(f"Unknown docker command: {command}", -1)


@task(pre=[call(docker, command="up")])
def up(c: Context):
    pass


@task(pre=[call(docker, command="down")])
def down(c: Context):
    pass


@task
def requirements(c: Context, upgrade_all=False, upgrade_package=None):
    if upgrade_all and upgrade_package:
        raise Exit("Cannot specify both upgrade and upgrade-package", -1)
    args = " -U" if upgrade_all else ""
    has_uv = c.run("uv -V", hide=True, timeout=1, warn=True)
    if has_uv.ok:
        cmd_base = "uv pip compile --no-strip-extras"
    else:
        cmd_base = "pip-compile --resolver=backtracking"
    env = {"CUSTOM_COMPILE_COMMAND": "inv requirements", "UV_CUSTOM_COMPILE_COMMAND": "inv requirements"}
    if upgrade_package:
        cmd_base += f" --upgrade-package {upgrade_package}"

    def _compile(base_path):
        with c.cd("requirements"):
            c.run(f"{cmd_base} {base_path}.in -o {base_path}.txt{args}", env=env)

    _compile("requirements")
    _compile("dev-requirements")
    _compile("prod-requirements")

    if _confirm("\nDo you want to sync your venv with the new requirements?", _exit=False):
        if has_uv.ok:
            result = c.run("uv pip sync --dry-run dev-requirements.txt", echo=True, pty=True)
            if "no changes" in result.stdout:
                return

            if _confirm("Do you want to apply the changes?", _exit=False):
                c.run("uv pip sync dev-requirements.txt", echo=True, pty=True)
        else:
            c.run("pip-sync -a dev-requirements.txt", echo=True, pty=True)


@task
def translations(c: Context):
    c.run("python manage.py makemessages --all --ignore node_modules --ignore venv")
    c.run("python manage.py makemessages -d djangojs --all --ignore node_modules --ignore venv")
    c.run("python manage.py compilemessages")


@task
def schema(c: Context):
    c.run("python manage.py spectacular --file api_schema.yaml")


@task
def setup_dev_env(c: Context, step=False):
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
        cprint("\nSkipping font end build. Run 'inv npm --install' once you have upgraded node.", "yellow")
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
    ver = LooseVersion(version)
    return ver >= LooseVersion(MIN_NODE_VERSION)


@task
def ngrok_url(c: Context):
    #  You need to have ngrok installed on your system
    c.run("ngrok http 8000", echo=True, asynchronous=True)
    while True:
        try:
            response = requests.get("http://localhost:4040/api/tunnels")
            if response.status_code == 200:
                public_url = response.json()["tunnels"][0]["public_url"].split("https://")[1]
                break
        except Exception:
            time.sleep(1)
            print("Trying to a public address from ngrok")

    print(f"Public address found: {public_url}")
    return public_url


@task(aliases=["django"])
def runserver(c: Context, public=False):
    runserver_command = "python manage.py runserver"
    if public:
        public_url = ngrok_url(c)
        runserver_command = f"SITE_URL_ROOT={public_url} {runserver_command}"
    c.run(runserver_command, echo=True, pty=True)


@task
def celery(c: Context, gevent=False):
    cmd = "celery -A gpt_playground worker -l INFO"
    if gevent:
        cmd += " --pool gevent --concurrency 10"
    else:
        cmd += " -B --pool=solo"

    if gevent:
        cprint("Starting celery worker with gevent pool. This will not run celery beat.", "yellow")
    c.run(f'watchfiles --filter python "{cmd}"', echo=True, pty=True)


@task
def ruff(c: Context, no_fix=False, unsafe_fixes=False):
    """Run ruff checks and formatting. Use --unsafe-fixes to apply unsafe fixes."""
    fix_flag = "" if no_fix else "--fix"
    unsafe_fixes_flag = "--unsafe-fixes" if unsafe_fixes else ""
    c.run(f"ruff check {fix_flag} {unsafe_fixes_flag}", echo=True, pty=True)
    c.run("ruff format", echo=True, pty=True)


@task
def npm(c: Context, watch=False, install=False):
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
