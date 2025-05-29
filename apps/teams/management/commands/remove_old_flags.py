import os
import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.teams.models import Flag


class Command(BaseCommand):
    help = "Remove old feature flags from the database after checking for usage in code"

    def add_arguments(self, parser):
        parser.add_argument(
            "--flag-name",
            type=str,
            help="Name of specific flag to remove. If not provided, will scan for all unused flags.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be deleted without actually deleting anything",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Skip confirmation prompt and delete flags immediately",
        )
        parser.add_argument(
            "--exclude-dirs",
            nargs="*",
            default=["node_modules", ".git", "__pycache__", ".venv", "venv", "env"],
            help="Directories to exclude from code search (default: node_modules, .git, __pycache__, .venv, venv, env)",
        )

    def handle(self, *args, **options):
        self.dry_run = options["dry_run"]
        self.force = options["force"]
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
        unused_flags = []

        for flag in flags:
            usages = self._find_flag_usages(flag.name)
            if not usages:
                unused_flags.append(flag)

        if not unused_flags:
            self.stdout.write(self.style.SUCCESS("No unused flags found"))
            return

        self.stdout.write(f"\nFound {len(unused_flags)} unused flags:")
        for flag in unused_flags:
            self.stdout.write(f"  - {flag.name}")

        if self.dry_run:
            self.stdout.write(self.style.WARNING("\nDry run mode - no flags will be deleted"))
            return

        if not self.force:
            confirm = input(f"\nAre you sure you want to delete {len(unused_flags)} unused flags? [y/N]: ")
            if confirm.lower() != "y":
                self.stdout.write("Operation cancelled")
                return

        self._delete_flags(unused_flags)

    def _process_single_flag(self, flag):
        usages = self._find_flag_usages(flag.name)
        
        if usages:
            self.stdout.write(f"Flag '{flag.name}' is still in use:")
            for file_path, lines in usages.items():
                self.stdout.write(f"\n  {file_path}:")
                for line_num, line_content in lines:
                    self.stdout.write(f"    Line {line_num}: {line_content.strip()}")
            
            if not self.force:
                self.stdout.write(self.style.WARNING(f"\nFlag '{flag.name}' appears to be in use. Use --force to delete anyway."))
                return
            else:
                self.stdout.write(self.style.WARNING(f"Force deleting flag '{flag.name}' despite usage"))

        if self.dry_run:
            self.stdout.write(self.style.WARNING(f"Dry run mode - flag '{flag.name}' would be deleted"))
            return

        if not usages and not self.force:
            confirm = input(f"Are you sure you want to delete flag '{flag.name}'? [y/N]: ")
            if confirm.lower() != "y":
                self.stdout.write("Operation cancelled")
                return

        self._delete_flags([flag])

    def _find_flag_usages(self, flag_name):
        """Find usages of a flag name in the codebase using regex patterns."""
        usages = {}
        project_root = Path(__file__).parent.parent.parent.parent.parent
        
        # Common patterns for flag usage
        patterns = [
            rf"flag_is_active\s*\(\s*['\"]({re.escape(flag_name)})['\"]",  # waffle.flag_is_active('flag_name')
            rf"get_waffle_flag\s*\(\s*['\"]({re.escape(flag_name)})['\"]",  # custom flag getter
            rf"['\"]({re.escape(flag_name)})['\"]\s*,?\s*request",  # 'flag_name', request pattern
            rf"Flag\.objects\.get\s*\(\s*name\s*=\s*['\"]({re.escape(flag_name)})['\"]",  # Flag.objects.get(name='flag_name')
            rf"['\"]({re.escape(flag_name)})['\"]",  # simple string match (less precise but catches more cases)
        ]
        
        compiled_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
        
        for file_path in self._get_source_files(project_root):
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                    
                file_usages = []
                for line_num, line in enumerate(lines, 1):
                    for pattern in compiled_patterns:
                        if pattern.search(line):
                            file_usages.append((line_num, line))
                            break  # Only count each line once
                
                if file_usages:
                    relative_path = file_path.relative_to(project_root)
                    usages[str(relative_path)] = file_usages
                    
            except (OSError, UnicodeDecodeError):
                # Skip files that can't be read
                continue
                
        return usages

    def _get_source_files(self, root_path):
        """Generator that yields all source files, excluding specified directories."""
        source_extensions = {'.py', '.js', '.ts', '.jsx', '.tsx', '.html', '.md', '.txt', '.yml', '.yaml', '.json'}
        
        for root, dirs, files in os.walk(root_path):
            # Remove excluded directories from dirs list to avoid walking them
            dirs[:] = [d for d in dirs if d not in self.exclude_dirs]
            
            for file in files:
                file_path = Path(root) / file
                if file_path.suffix.lower() in source_extensions:
                    yield file_path

    @transaction.atomic
    def _delete_flags(self, flags):
        """Delete the specified flags from the database."""
        count = len(flags)
        flag_names = [flag.name for flag in flags]
        
        # Delete the flags
        Flag.objects.filter(name__in=flag_names).delete()
        
        self.stdout.write(
            self.style.SUCCESS(f"Successfully deleted {count} flag{'s' if count != 1 else ''}: {', '.join(flag_names)}")
        )