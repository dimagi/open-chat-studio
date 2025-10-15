import os
import re
from collections import defaultdict
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.teams.models import Flag


class Command(BaseCommand):
    help = "List feature flags and their usage status in code"

    def add_arguments(self, parser):
        parser.add_argument(
            "--flag-name",
            type=str,
            help="Name of specific flag to check. If not provided, will scan all flags.",
        )
        parser.add_argument(
            "--exclude-dirs",
            nargs="*",
            default=["node_modules", ".git", "__pycache__", ".venv", "venv", "env"],
            help="Directories to exclude from code search (default: node_modules, .git, __pycache__, .venv, venv, env)",
        )

    def handle(self, *args, **options):
        self.exclude_dirs = set(options["exclude_dirs"])

        if options["flag_name"]:
            try:
                flag = Flag.objects.get(name=options["flag_name"])
                self._process_single_flag(flag)
            except Flag.DoesNotExist:
                raise CommandError(f"Flag '{options['flag_name']}' does not exist") from None
        else:
            self._process_all_flags()

    def _process_all_flags(self):
        flags = Flag.objects.all()
        if not flags.exists():
            self.stdout.write(self.style.WARNING("No flags found in database"))
            return

        self.stdout.write(f"Found {flags.count()} flags in database")

        # Find usages for all flags at once
        all_flag_usages = self._find_all_flag_usages([flag.name for flag in flags])

        used_flags = []
        unused_flags = []

        for flag in flags:
            if flag.name in all_flag_usages and all_flag_usages[flag.name]:
                used_flags.append(flag)
            else:
                unused_flags.append(flag)

        if used_flags:
            self.stdout.write(f"\nFlags found in code ({len(used_flags)}):")
            for flag in used_flags:
                self.stdout.write(f"  ✓ {flag.name}")
                for file_path in all_flag_usages[flag.name]:
                    self.stdout.write(f"    - {file_path}")

        if unused_flags:
            self.stdout.write(f"\nFlags not found in code ({len(unused_flags)}):")
            for flag in unused_flags:
                self.stdout.write(f"  ✗ {flag.name}")

        if not used_flags and not unused_flags:
            self.stdout.write(self.style.SUCCESS("No flags to analyze"))

    def _process_single_flag(self, flag):
        usages = self._find_all_flag_usages([flag.name])

        if usages and flag.name in usages and usages[flag.name]:
            self.stdout.write(f"✓ Flag '{flag.name}' is used in code:")
            for file_path in usages[flag.name]:
                self.stdout.write(f"  - {file_path}")
        else:
            self.stdout.write(f"✗ Flag '{flag.name}' not found in code")

    def _find_all_flag_usages(self, flag_names):
        """Find usages of all flag names in the codebase using a single pass."""
        if not flag_names:
            return {}

        all_usages = {flag_name: defaultdict(list) for flag_name in flag_names}
        project_root = settings.BASE_DIR

        # legacy flag names might not be prefixed with 'flag_'
        escaped_flags = [re.escape(flag_name) for flag_name in flag_names]
        flag_pattern = "|".join(escaped_flags)

        # new style flags should start with 'flag_'
        flags_with_prefix = "|".join(
            [re.escape(flag_name) for flag_name in flag_names if flag_name.startswith("flag_")]
        )

        patterns = [
            rf"['\"]((?:{flags_with_prefix}))['\"]\)",
            # waffle.flag_is_active(request, 'flag_name')
            rf"flag_is_active\s*\([a-zA-Z.]+,\s*['\"]((?:{flag_pattern}))['\"]\)",
            rf"get_waffle_flag\s*\(\s*['\"]((?:{flag_pattern}))['\"]\)",  # custom flag getter
            rf"['\"]((?:{flag_pattern}))['\"]\s*,?\s*request",  # 'flag_name', request pattern
            rf"request,?\s*['\"]((?:{flag_pattern}))['\"]",  # request, 'flag_name' pattern
            # Flag.objects.get(name='flag_name')
            rf"Flag\.objects\.get\s*\(\s*name\s*=\s*['\"]((?:{flag_pattern}))['\"]\)",
            # Flag.get(name='flag_name') or Flag.get('flag_name')
            rf"Flag\.get\s*\(\s*(?:name\s*=\s*)?['\"]((?:{flag_pattern}))['\"]\)",
            # {% flag 'flag_name' %}
            rf"\{{%\s*flag\s+['\"]((?:{flag_pattern}))['\"]\s*%\}}",
            # @override_flag("flag_name", active=True)
            rf"@override_flag\s*\(\s*['\"]((?:{flag_pattern}))['\"]",
            # flag_required="flag_name"
            rf"flag_required\s*=\s*['\"]((?:{flag_pattern}))['\"]",
        ]

        compiled_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in patterns]

        for file_path in self._get_source_files(project_root):
            try:
                file_content = file_path.read_text(encoding="utf-8")
                relative_path = str(file_path.relative_to(project_root))

                for pattern in compiled_patterns:
                    matches = pattern.findall(file_content)
                    if matches:
                        for match in matches:
                            if match in all_usages:
                                all_usages[match][relative_path].append(match)
            except (OSError, UnicodeDecodeError):
                # Skip files that can't be read
                continue

        return all_usages

    def _get_source_files(self, root_path):
        """Generator that yields all source files, excluding specified directories."""
        source_extensions = {".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".md", ".txt", ".yml", ".yaml", ".json"}
        ignored_files = {"flags.py"}
        ignored_paths = {"apps/teams/flags.py"}

        for root, dirs, files in os.walk(root_path):
            # Remove excluded directories from dirs list to avoid walking them
            dirs[:] = [d for d in dirs if d not in self.exclude_dirs]

            for file in files:
                file_path = Path(root) / file

                if file in ignored_files and str(file_path.relative_to(root_path)) in ignored_paths:
                    continue

                if file_path.suffix.lower() in source_extensions and file_path not in ignored_files:
                    yield file_path
