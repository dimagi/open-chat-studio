#!/usr/bin/env python3
"""
Targeted cleanup script that only removes specific experiment-only functions
while preserving the shared views and templates.
"""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

# Functions to remove from experiment.py (these are experiment-only UI functions)
FUNCTIONS_TO_REMOVE = [
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


def clean_experiment_views():
    """Clean the experiment.py views file by removing specific functions"""
    experiment_file = PROJECT_ROOT / "apps" / "experiments" / "views" / "experiment.py"

    with open(experiment_file, encoding="utf-8") as f:
        content = f.read()

    original_content = content

    for func_name in FUNCTIONS_TO_REMOVE:
        # Pattern to match function definition and its body
        # This matches: def function_name(...): and everything until the next def/class/end of file
        pattern = rf"(^@[^\n]*\n)*^def\s+{re.escape(func_name)}\s*\([^)]*\):.*?(?=\n^(?:def|class|@|\Z))"
        content = re.sub(pattern, "", content, flags=re.MULTILINE | re.DOTALL)

    # Clean up extra blank lines
    content = re.sub(r"\n\s*\n\s*\n", "\n\n", content)

    if content != original_content:
        with open(experiment_file, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"✓ Cleaned {experiment_file}")
        return True

    return False


def clean_urls():
    """Clean the URLs file by commenting out routes to removed views"""
    urls_file = PROJECT_ROOT / "apps" / "experiments" / "urls.py"

    with open(urls_file, encoding="utf-8") as f:
        content = f.read()

    original_content = content

    # Comment out URL patterns for removed views
    for func_name in FUNCTIONS_TO_REMOVE:
        # Find lines that reference this view function
        pattern = rf"(.*{re.escape(func_name)}.*)"
        replacement = r"    # \1  # Removed - experiment UI only"
        content = re.sub(pattern, replacement, content)

    if content != original_content:
        with open(urls_file, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"✓ Cleaned {urls_file}")
        return True

    return False


def clean_init_file():
    """Clean the __init__.py file by removing imports for deleted functions"""
    init_file = PROJECT_ROOT / "apps" / "experiments" / "views" / "__init__.py"

    with open(init_file, encoding="utf-8") as f:
        content = f.read()

    original_content = content

    # Remove imports for deleted functions
    for func_name in FUNCTIONS_TO_REMOVE:
        # Remove the function from import lines
        pattern = rf",\s*{re.escape(func_name)}(?=\s*[,\)])"
        content = re.sub(pattern, "", content)
        pattern = rf"{re.escape(func_name)},\s*"
        content = re.sub(pattern, "", content)
        # Handle case where it's the only import
        pattern = rf"^\s*{re.escape(func_name)}\s*$"
        content = re.sub(pattern, "", content, flags=re.MULTILINE)

    # Remove imports from deleted view files
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

    # Clean up extra blank lines and commas
    content = re.sub(r"\n\s*\n\s*\n", "\n\n", content)
    content = re.sub(r",\s*\n\s*\)", "\n)", content)
    content = re.sub(r",\s*,", ",", content)

    if content != original_content:
        with open(init_file, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"✓ Cleaned {init_file}")
        return True

    return False


if __name__ == "__main__":
    print("Running targeted cleanup...")

    changes_made = False
    changes_made |= clean_experiment_views()
    changes_made |= clean_urls()
    changes_made |= clean_init_file()

    if changes_made:
        print("✅ Targeted cleanup complete")
    else:
        print("ℹ️  No changes needed")
