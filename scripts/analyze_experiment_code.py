#!/usr/bin/env python3
"""
Analysis script to identify experiment-only views and templates that can be safely removed.

This script analyzes the codebase to find:
1. Views that are only referenced by experiment URLs
2. Templates that are only used by experiment views
3. Code that references experiment models vs chatbot models
"""

import re
from collections import defaultdict, namedtuple
from pathlib import Path

# Define the project root
PROJECT_ROOT = Path(__file__).parent.parent
APPS_DIR = PROJECT_ROOT / "apps"
TEMPLATES_DIR = PROJECT_ROOT / "templates"

CodeReference = namedtuple("CodeReference", ["file_path", "line_number", "line_content"])


class ExperimentCodeAnalyzer:
    def __init__(self):
        self.experiment_views = set()
        self.chatbot_views = set()
        self.experiment_templates = set()
        self.chatbot_templates = set()
        self.experiment_urls = set()
        self.chatbot_urls = set()
        self.view_references = defaultdict(list)
        self.template_references = defaultdict(list)

    def analyze_urls(self):
        """Analyze URL patterns to identify experiment vs chatbot URLs"""
        print("Analyzing URL patterns...")

        # Experiment URLs
        experiment_urls_file = APPS_DIR / "experiments" / "urls.py"
        if experiment_urls_file.exists():
            with open(experiment_urls_file) as f:
                content = f.read()
                # Extract view function names from URLs
                view_pattern = r"views\.(\w+)"
                for match in re.finditer(view_pattern, content):
                    self.experiment_views.add(match.group(1))

        # Chatbot URLs
        chatbot_urls_file = APPS_DIR / "chatbots" / "urls.py"
        if chatbot_urls_file.exists():
            with open(chatbot_urls_file) as f:
                content = f.read()
                # Extract view function names from URLs
                view_pattern = r"views\.(\w+)"
                for match in re.finditer(view_pattern, content):
                    self.chatbot_views.add(match.group(1))

    def analyze_views(self):
        """Analyze view files to understand which views exist"""
        print("Analyzing view files...")

        # Experiment views
        experiment_views_dir = APPS_DIR / "experiments" / "views"
        if experiment_views_dir.exists():
            for view_file in experiment_views_dir.glob("*.py"):
                with open(view_file) as f:
                    content = f.read()
                    # Extract function and class definitions
                    func_pattern = r"def\s+(\w+)\s*\("
                    class_pattern = r"class\s+(\w+)\s*\("

                    for match in re.finditer(func_pattern, content):
                        self.experiment_views.add(match.group(1))
                    for match in re.finditer(class_pattern, content):
                        self.experiment_views.add(match.group(1))

        # Chatbot views
        chatbot_views_file = APPS_DIR / "chatbots" / "views.py"
        if chatbot_views_file.exists():
            with open(chatbot_views_file) as f:
                content = f.read()
                # Extract function and class definitions
                func_pattern = r"def\s+(\w+)\s*\("
                class_pattern = r"class\s+(\w+)\s*\("

                for match in re.finditer(func_pattern, content):
                    self.chatbot_views.add(match.group(1))
                for match in re.finditer(class_pattern, content):
                    self.chatbot_views.add(match.group(1))

    def analyze_templates(self):
        """Analyze template usage to identify experiment-only templates"""
        print("Analyzing template usage...")

        # Find all experiment templates
        experiment_templates_dir = TEMPLATES_DIR / "experiments"
        if experiment_templates_dir.exists():
            for template_file in experiment_templates_dir.rglob("*.html"):
                rel_path = template_file.relative_to(TEMPLATES_DIR)
                self.experiment_templates.add(str(rel_path))

        # Find all chatbot templates
        chatbot_templates_dir = TEMPLATES_DIR / "chatbots"
        if chatbot_templates_dir.exists():
            for template_file in chatbot_templates_dir.rglob("*.html"):
                rel_path = template_file.relative_to(TEMPLATES_DIR)
                self.chatbot_templates.add(str(rel_path))

    def find_template_references(self):
        """Find where templates are referenced in the codebase"""
        print("Finding template references...")

        # Search for template references in Python files
        for py_file in APPS_DIR.rglob("*.py"):
            try:
                with open(py_file, encoding="utf-8") as f:
                    lines = f.readlines()
                    for line_num, line in enumerate(lines, 1):
                        # Look for render patterns
                        render_patterns = [
                            r'render\([^,]+,\s*["\']([^"\']+)["\']',
                            r'get_template\(["\']([^"\']+)["\']',
                            r'template_name\s*=\s*["\']([^"\']+)["\']',
                        ]

                        for pattern in render_patterns:
                            matches = re.finditer(pattern, line)
                            for match in matches:
                                template_name = match.group(1)
                                if template_name.startswith("experiments/") or template_name.startswith("chatbots/"):
                                    self.template_references[template_name].append(
                                        CodeReference(str(py_file), line_num, line.strip())
                                    )
            except (UnicodeDecodeError, PermissionError):
                continue

    def find_model_references(self):
        """Find references to Experiment model vs chatbot usage"""
        print("Finding model references...")

        experiment_model_patterns = [
            r"Experiment\.",
            r"ExperimentSession\.",
            r"from.*experiments.*import.*Experiment",
            r"experiments\.Experiment",
        ]

        chatbot_patterns = [
            r"chatbot",
            r"Chatbot",
        ]

        for py_file in APPS_DIR.rglob("*.py"):
            try:
                with open(py_file, encoding="utf-8") as f:
                    content = f.read()

                    # Check for experiment model usage
                    for pattern in experiment_model_patterns:
                        if re.search(pattern, content, re.IGNORECASE):
                            print(f"Experiment model reference in: {py_file}")
                            break

                    # Check for chatbot usage
                    for pattern in chatbot_patterns:
                        if re.search(pattern, content, re.IGNORECASE):
                            print(f"Chatbot reference in: {py_file}")
                            break

            except (UnicodeDecodeError, PermissionError):
                continue

    def identify_safe_to_remove(self):
        """Identify views and templates that are safe to remove"""
        print("\n" + "=" * 60)
        print("ANALYSIS RESULTS")
        print("=" * 60)

        print(f"\nExperiment Views ({len(self.experiment_views)}):")
        for view in sorted(self.experiment_views):
            print(f"  - {view}")

        print(f"\nChatbot Views ({len(self.chatbot_views)}):")
        for view in sorted(self.chatbot_views):
            print(f"  - {view}")

        print(f"\nExperiment Templates ({len(self.experiment_templates)}):")
        for template in sorted(self.experiment_templates):
            print(f"  - {template}")

        print(f"\nChatbot Templates ({len(self.chatbot_templates)}):")
        for template in sorted(self.chatbot_templates):
            print(f"  - {template}")

        # Check for shared views
        shared_views = self.experiment_views & self.chatbot_views
        experiment_only_views = self.experiment_views - self.chatbot_views

        print(f"\nShared Views ({len(shared_views)}):")
        for view in sorted(shared_views):
            print(f"  - {view} (KEEP - used by both)")

        print(f"\nExperiment-Only Views ({len(experiment_only_views)}):")
        for view in sorted(experiment_only_views):
            print(f"  - {view} (CANDIDATE FOR REMOVAL)")

        # Analyze template references
        print("\nTemplate Reference Analysis:")
        experiment_only_templates = []
        shared_templates = []

        for template in self.experiment_templates:
            refs = self.template_references.get(template, [])
            if not refs:
                experiment_only_templates.append(template)
                continue

            # Check if references are only from experiment views
            non_experiment_refs = []
            for ref in refs:
                if "experiments" not in str(ref.file_path):
                    non_experiment_refs.append(ref)

            if non_experiment_refs:
                shared_templates.append((template, refs))
            else:
                experiment_only_templates.append(template)

        print(f"\nExperiment-Only Templates ({len(experiment_only_templates)}):")
        for template in experiment_only_templates:
            print(f"  - {template} (CANDIDATE FOR REMOVAL)")

        print(f"\nShared Templates ({len(shared_templates)}):")
        for template, refs in shared_templates:
            print(f"  - {template} (KEEP - used outside experiments)")
            for ref in refs[:3]:  # Show first 3 references
                print(f"    * {ref.file_path}:{ref.line_number}")

        return {
            "experiment_only_views": experiment_only_views,
            "experiment_only_templates": experiment_only_templates,
            "shared_views": shared_views,
            "shared_templates": [t[0] for t in shared_templates],
        }

    def run_analysis(self):
        """Run the complete analysis"""
        print("Starting Experiment Code Analysis...")
        print("=" * 60)

        self.analyze_urls()
        self.analyze_views()
        self.analyze_templates()
        self.find_template_references()

        results = self.identify_safe_to_remove()

        # Save results to file
        output_file = PROJECT_ROOT / "experiment_cleanup_analysis.txt"
        with open(output_file, "w") as f:
            f.write("EXPERIMENT CLEANUP ANALYSIS RESULTS\n")
            f.write("=" * 50 + "\n\n")

            f.write("EXPERIMENT-ONLY VIEWS (Safe to remove):\n")
            for view in sorted(results["experiment_only_views"]):
                f.write(f"  - {view}\n")

            f.write("\nEXPERIMENT-ONLY TEMPLATES (Safe to remove):\n")
            for template in sorted(results["experiment_only_templates"]):
                f.write(f"  - {template}\n")

            f.write("\nSHARED VIEWS (Keep):\n")
            for view in sorted(results["shared_views"]):
                f.write(f"  - {view}\n")

            f.write("\nSHARED TEMPLATES (Keep):\n")
            for template in sorted(results["shared_templates"]):
                f.write(f"  - {template}\n")

        print(f"\nResults saved to: {output_file}")
        return results


if __name__ == "__main__":
    analyzer = ExperimentCodeAnalyzer()
    analyzer.run_analysis()
