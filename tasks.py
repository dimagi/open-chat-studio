import time
from pathlib import Path

import requests
from invoke import Context, Exit, call, task
from termcolor import cprint


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
    cmd_base = "pip-compile --resolver=backtracking"
    env = {"CUSTOM_COMPILE_COMMAND": "inv requirements"}
    if upgrade_package:
        cmd_base += f" --upgrade-package {upgrade_package}"
    c.run(f"{cmd_base} requirements/requirements.in{args}", env=env)
    c.run(f"{cmd_base} requirements/dev-requirements.in{args}", env=env)
    c.run(f"{cmd_base} requirements/prod-requirements.in{args}", env=env)


@task
def translations(c: Context):
    c.run("python manage.py makemessages --all --ignore node_modules --ignore venv")
    c.run("python manage.py makemessages -d djangojs --all --ignore node_modules --ignore venv")
    c.run("python manage.py compilemessages")


@task
def schema(c: Context):
    c.run("python manage.py spectacular --file api_schema.yaml")


@task
def setup_dev_env(c: Context):
    cprint("Setting up dev environment", "green")
    docker(c, command="up")

    cprint("\nInstalling pre-commit hooks", "green")
    c.run("pre-commit install --install-hooks", echo=True)

    if not Path(".env").exists():
        cprint("\nCreating .env file", "green")
        c.run("cp .env.example .env", echo=True)
    else:
        print("\nSkipping .env file creation, file already exists")

    cprint("\nRunning DB migrations", "green")
    c.run("python manage.py migrate", echo=True)

    cprint("\nInstalling npm packages", "green")
    c.run("npm install", echo=True)

    cprint("\nBuilding JS & CSS resources", "green")
    c.run("npm run dev", echo=True)

    cprint("\nCreating superuser", "green")
    c.run("python manage.py createsuperuser", echo=True, pty=True)


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


@task
def runserver(c: Context, public=False):
    runserver_command = "python manage.py runserver"
    if public:
        public_url = ngrok_url(c)
        runserver_command = f"SITE_URL_ROOT={public_url} {runserver_command}"
    c.run(runserver_command, echo=True, pty=True)


@task
def celery(c: Context):
    c.run('watchfiles --filter python "celery -A gpt_playground worker -l INFO -B"', echo=True, pty=True)


@task
def ruff(c: Context, no_fix=False, unsafe_fixes=False):
    """Run ruff checks and formatting. Use --unsafe-fixes to apply unsafe fixes."""
    fix_flag = "" if no_fix else "--fix"
    unsafe_fixes_flag = "--unsafe-fixes" if unsafe_fixes else ""
    c.run(f"ruff check {fix_flag} {unsafe_fixes_flag}", echo=True, pty=True)
    c.run("ruff format", echo=True, pty=True)
