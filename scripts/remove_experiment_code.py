#!/usr/bin/env python3
"""
Script to systematically remove experiment-only views and templates.

This script removes:
1. Experiment-only templates that are not used by chatbots
2. Experiment-only views that are not referenced elsewhere
3. URL patterns that only reference removed views
4. Related imports and references

It preserves:
- Shared templates used by chatbots
- Shared views used by chatbots
- Any code referenced outside of experiments
"""

import re
import shutil
from datetime import datetime
from pathlib import Path

# Define the project root
PROJECT_ROOT = Path(__file__).parent.parent
APPS_DIR = PROJECT_ROOT / "apps"
TEMPLATES_DIR = PROJECT_ROOT / "templates"


class ExperimentCodeRemover:
    def __init__(self, dry_run=True):
        self.dry_run = dry_run
        self.removed_files = []
        self.modified_files = []

        # Templates to remove (experiment-only)
        self.templates_to_remove = [
            "experiments/chat/ai_message.html",
            "experiments/chat/chat_message_response.html",
            "experiments/chat/chat_ui.html",
            "experiments/chat/components/message_rating.html",
            "experiments/chat/components/system_icon.html",
            "experiments/chat/components/trace_icons.html",
            "experiments/chat/components/user_icon.html",
            "experiments/chat/end_experiment_modal.html",
            "experiments/chat/experiment_response_htmx.html",
            "experiments/chat/human_message.html",
            "experiments/chat/input_bar.html",
            "experiments/chat/system_message.html",
            "experiments/components/experiment_chat.html",
            "experiments/components/experiment_details.html",
            "experiments/components/experiment_version_actions.html",
            "experiments/components/experiment_version_cell.html",
            "experiments/components/experiment_version_details_content.html",
            "experiments/components/exports.html",
            "experiments/components/pagination_buttons.html",
            "experiments/components/prompt_builder_experiments_list_sidebar.html",
            "experiments/components/prompt_builder_history_sidebar.html",
            "experiments/components/prompt_builder_message_list.html",
            "experiments/components/prompt_builder_prompt_input.html",
            "experiments/components/prompt_builder_source_material_sidebar.html",
            "experiments/components/prompt_builder_toolbox.html",
            "experiments/components/unreleased_badge.html",
            "experiments/components/user_comments.html",
            "experiments/components/versions/compare.html",
            "experiments/components/versions/version_field.html",
            "experiments/create_version_button.html",
            "experiments/email/invitation.html",
            "experiments/email/safety_violation.html",
            "experiments/email/verify_public_chat_email.html",
            "experiments/experiment_chat.html",
            "experiments/experiment_complete.html",
            "experiments/experiment_form.html",
            "experiments/experiment_invitations.html",
            "experiments/experiment_list.html",
            "experiments/experiment_review.html",
            "experiments/experiment_session_view.html",
            "experiments/filters.html",
            "experiments/manage/invite_row.html",
            "experiments/pre_survey.html",
            "experiments/prompt_builder.html",
            "experiments/share/dialog.html",
            "experiments/share/widget.html",
            "experiments/single_experiment_home.html",
            "experiments/source_material_list.html",
            "experiments/start_experiment_session.html",
        ]

        # Templates to keep (shared with chatbots)
        self.templates_to_keep = [
            "experiments/components/experiment_actions_column.html",
            "experiments/create_version_form.html",
            "experiments/experiment_version_table.html",
        ]

        # View files to completely remove (experiment-specific functionality)
        self.view_files_to_remove = [
            "apps/experiments/views/chat.py",  # Chat interface views
            "apps/experiments/views/consent.py",  # Consent form views
            "apps/experiments/views/prompt.py",  # Prompt builder views
            "apps/experiments/views/safety.py",  # Safety layer views
            "apps/experiments/views/source_material.py",  # Source material views
            "apps/experiments/views/survey.py",  # Survey views
        ]

        # View files to partially modify (remove experiment-only functions)
        self.views_to_modify = {
            "apps/experiments/views/experiment.py": [
                # Functions to remove (experiment-only)
                "experiments_home",
                "experiments_prompt_builder",
                "experiments_prompt_builder_get_message",
                "get_prompt_builder_message_response",
                "get_prompt_builder_history",
                "prompt_builder_start_save_process",
                "prompt_builder_load_experiments",
                "prompt_builder_load_source_material",
                "single_experiment_home",
                "experiment_chat",
                "experiment_chat_embed",
                "experiment_chat_session",
                "experiment_complete",
                "experiment_invitations",
                "experiment_pre_survey",
                "experiment_review",
                "experiment_session_details_view",
                "experiment_session_message",
                "experiment_session_message_embed",
                "experiment_session_messages_view",
                "experiment_session_pagination_view",
                "experiment_version_details",
                "archive_experiment_version",
                "delete_experiment",
                "download_file",
                "end_experiment",
                "generate_chat_export",
                "get_export_download_link",
                "get_image_html",
                "get_message_response",
                "poll_messages",
                "poll_messages_embed",
                "rate_message",
                "send_invitation",
                "set_default_experiment",
                "start_session_from_invite",
                "start_session_public",
                "start_session_public_embed",
                "trends_data",
                "update_version_description",
                "verify_public_chat_token",
                "version_create_status",
            ]
        }

    def backup_files(self):
        """Create a backup of files before modification"""
        if not self.dry_run:
            backup_dir = PROJECT_ROOT / f"backup_experiment_cleanup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            backup_dir.mkdir(exist_ok=True)

            # Backup experiments directory
            experiments_backup = backup_dir / "experiments"
            if (APPS_DIR / "experiments").exists():
                shutil.copytree(APPS_DIR / "experiments", experiments_backup)

            # Backup experiment templates
            templates_backup = backup_dir / "templates" / "experiments"
            if (TEMPLATES_DIR / "experiments").exists():
                shutil.copytree(TEMPLATES_DIR / "experiments", templates_backup)

            print(f"✓ Backup created at: {backup_dir}")

    def remove_templates(self):
        """Remove experiment-only templates"""
        print("\nRemoving experiment-only templates...")

        for template_path in self.templates_to_remove:
            full_path = TEMPLATES_DIR / template_path
            if full_path.exists():
                if self.dry_run:
                    print(f"  [DRY RUN] Would remove: {template_path}")
                else:
                    full_path.unlink()
                    self.removed_files.append(str(full_path))
                    print(f"  ✓ Removed: {template_path}")
            else:
                print(f"  - Not found: {template_path}")

        # Clean up empty directories
        if not self.dry_run:
            self._cleanup_empty_directories(TEMPLATES_DIR / "experiments")

    def remove_view_files(self):
        """Remove entire view files that are experiment-only"""
        print("\nRemoving experiment-only view files...")

        for view_file_path in self.view_files_to_remove:
            full_path = PROJECT_ROOT / view_file_path
            if full_path.exists():
                if self.dry_run:
                    print(f"  [DRY RUN] Would remove: {view_file_path}")
                else:
                    full_path.unlink()
                    self.removed_files.append(str(full_path))
                    print(f"  ✓ Removed: {view_file_path}")
            else:
                print(f"  - Not found: {view_file_path}")

    def modify_view_files(self):
        """Remove experiment-only functions from shared view files"""
        print("\\nModifying shared view files...")

        for view_file_path, functions_to_remove in self.views_to_modify.items():
            full_path = PROJECT_ROOT / view_file_path
            if not full_path.exists():
                print(f"  - Not found: {view_file_path}")
                continue

            if self.dry_run:
                print(f"  [DRY RUN] Would modify: {view_file_path}")
                print(f"    Functions to remove: {', '.join(functions_to_remove[:3])}...")
                continue

            # Read the file
            with open(full_path, encoding="utf-8") as f:
                content = f.read()

            original_content = content

            # Remove functions
            for func_name in functions_to_remove:
                # Pattern to match function definition and its body
                pattern = rf"def\s+{re.escape(func_name)}\s*\([^)]*\):[^\n]*(?:\n(?:\s.*|\n))*?(?=\n\S|\n$|$)"
                content = re.sub(pattern, "", content, flags=re.MULTILINE)

            # Clean up extra blank lines
            content = re.sub(r"\n\s*\n\s*\n", "\n\n", content)

            if content != original_content:
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)
                self.modified_files.append(str(full_path))
                print(f"  ✓ Modified: {view_file_path}")

    def update_urls_py(self):
        """Remove URL patterns that reference removed views"""
        print("\\nUpdating URL patterns...")

        urls_file = APPS_DIR / "experiments" / "urls.py"
        if not urls_file.exists():
            print("  - URLs file not found")
            return

        if self.dry_run:
            print("  [DRY RUN] Would update experiments/urls.py")
            return

        with open(urls_file, encoding="utf-8") as f:
            content = f.read()

        original_content = content

        # Remove URL patterns for removed views
        removed_patterns = [
            "experiments_home",
            "experiments_prompt_builder",
            "single_experiment_home",
            "experiment_chat",
            "experiment_complete",
            "experiment_invitations",
            "start_session_public",
            "verify_public_chat_token",
            "rate_message",
        ]

        for pattern in removed_patterns:
            # Remove the entire path() statement for this view
            url_pattern = rf"\s*path\([^,]*{re.escape(pattern)}[^)]*\),?\n"
            content = re.sub(url_pattern, "", content, flags=re.MULTILINE)

        # Clean up extra blank lines and commas
        content = re.sub(r"\n\s*\n\s*\n", "\n\n", content)
        content = re.sub(r",\s*\n\s*\]", "\n]", content)

        if content != original_content:
            with open(urls_file, "w", encoding="utf-8") as f:
                f.write(content)
            self.modified_files.append(str(urls_file))
            print("  ✓ Updated experiments/urls.py")

    def update_views_init_py(self):
        """Update __init__.py to remove imports for deleted views"""
        print("\\nUpdating views/__init__.py...")

        init_file = APPS_DIR / "experiments" / "views" / "__init__.py"
        if not init_file.exists():
            print("  - __init__.py not found")
            return

        if self.dry_run:
            print("  [DRY RUN] Would update views/__init__.py")
            return

        with open(init_file, encoding="utf-8") as f:
            content = f.read()

        original_content = content

        # Remove imports from deleted files
        imports_to_remove = [
            r"from \.chat import .*",
            r"from \.consent import .*",
            r"from \.prompt import .*",
            r"from \.safety import .*",
            r"from \.source_material import .*",
            r"from \.survey import .*",
        ]

        for import_pattern in imports_to_remove:
            content = re.sub(import_pattern, "", content, flags=re.MULTILINE)

        # Clean up extra blank lines
        content = re.sub(r"\n\s*\n\s*\n", "\n\n", content)

        if content != original_content:
            with open(init_file, "w", encoding="utf-8") as f:
                f.write(content)
            self.modified_files.append(str(init_file))
            print("  ✓ Updated views/__init__.py")

    def _cleanup_empty_directories(self, directory):
        """Recursively remove empty directories"""
        if not directory.exists() or not directory.is_dir():
            return

        # Remove empty subdirectories first
        for subdir in directory.iterdir():
            if subdir.is_dir():
                self._cleanup_empty_directories(subdir)

        # Remove directory if it's empty
        try:
            if not any(directory.iterdir()):
                directory.rmdir()
                print(f"  ✓ Removed empty directory: {directory.relative_to(PROJECT_ROOT)}")
        except OSError:
            pass  # Directory not empty

    def generate_report(self):
        """Generate a summary report of changes"""
        print("\\n" + "=" * 60)
        print("CLEANUP SUMMARY")
        print("=" * 60)

        print(f"\\nRemoved Files ({len(self.removed_files)}):")
        for file_path in sorted(self.removed_files):
            rel_path = Path(file_path).relative_to(PROJECT_ROOT)
            print(f"  - {rel_path}")

        print(f"\\nModified Files ({len(self.modified_files)}):")
        for file_path in sorted(self.modified_files):
            rel_path = Path(file_path).relative_to(PROJECT_ROOT)
            print(f"  - {rel_path}")

        # Save report to file
        report_file = PROJECT_ROOT / "experiment_cleanup_report.txt"
        with open(report_file, "w") as f:
            f.write("EXPERIMENT CODE CLEANUP REPORT\\n")
            f.write("=" * 40 + "\\n\\n")
            f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\\n")
            f.write(f"Mode: {'DRY RUN' if self.dry_run else 'ACTUAL RUN'}\\n\\n")

            f.write(f"REMOVED FILES ({len(self.removed_files)}):  \\n")
            for file_path in sorted(self.removed_files):
                rel_path = Path(file_path).relative_to(PROJECT_ROOT)
                f.write(f"  - {rel_path}\\n")

            f.write(f"\\nMODIFIED FILES ({len(self.modified_files)}): \\n")
            for file_path in sorted(self.modified_files):
                rel_path = Path(file_path).relative_to(PROJECT_ROOT)
                f.write(f"  - {rel_path}\\n")

        print(f"\\nReport saved to: {report_file}")

    def run_cleanup(self):
        """Execute the complete cleanup process"""
        print("Starting Experiment Code Cleanup...")
        print("=" * 60)
        print(f"Mode: {'DRY RUN' if self.dry_run else 'ACTUAL RUN'}")

        if not self.dry_run:
            self.backup_files()

        self.remove_templates()
        self.remove_view_files()
        self.modify_view_files()
        self.update_urls_py()
        self.update_views_init_py()

        self.generate_report()

        if self.dry_run:
            print("\\n🔍 DRY RUN COMPLETE - No files were actually modified")
            print("   Run with --execute to perform actual cleanup")
        else:
            print("\\n✅ CLEANUP COMPLETE")
            print("   Remember to:")
            print("   1. Run tests to verify functionality")
            print("   2. Check for any remaining references")
            print("   3. Update any documentation")


if __name__ == "__main__":
    import sys

    # Check for --execute flag
    dry_run = "--execute" not in sys.argv

    if dry_run:
        print("🔍 Running in DRY RUN mode (no files will be modified)")
        print("   Use --execute flag to perform actual cleanup")
        print()

    remover = ExperimentCodeRemover(dry_run=dry_run)
    remover.run_cleanup()
