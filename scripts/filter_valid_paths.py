#!/usr/bin/env python
"""
This script filters valid paths from a CSV file based on Django URL patterns. This is mostly used to filter
logs from the AWS WAF or Load Balancer.

Usage:
    - To filter paths: `python scripts/filter_valid_paths.py input.csv output.csv`
    - To debug a specific path: `python scripts/filter_valid_paths.py --debug /path/to/debug`
    - To show all URL patterns: `python scripts/filter_valid_paths.py --show-paths`

The script uses Django's URL resolver to fetch all defined URL patterns and matches
them against the paths in the input CSV file. Valid paths are written to the output CSV file.

The CSV should have a header row and the script will try to identify the column containing the URL paths.
The following headers are considered for the URL path column:
    - path
    - url
    - url_path
    - pathname
    - route

If none of these headers are found, the script will default to using the first column as the URL path.
"""

import csv
import os
import re
import sys
import time
from pathlib import Path

# Setup Django environment
import django

# Set the DJANGO_SETTINGS_MODULE environment variable
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gpt_playground.settings")

# Configure Django
django.setup()

# Import Django's URL resolver
from django.urls import URLPattern, URLResolver, get_resolver  # noqa


def get_all_urls(resolver=None, prefix=""):
    """
    Get all URL patterns from Django
    """
    if resolver is None:
        resolver = get_resolver()

    url_patterns = []

    for pattern in resolver.url_patterns:
        if isinstance(pattern, URLPattern):
            pattern_str = prefix + str(pattern.pattern)
            # Convert Django regex format to a more readable path
            # Handle path converters like '<int:id>' or '<slug:team_slug>'
            pattern_str = re.sub(r"<(?:int|str|slug|uuid):\w+>", r"[variable]", pattern_str)
            # Replace standard named groups with [variable]
            pattern_str = re.sub(r"\(\?P<\w+>[^)]+\)", r"[variable]", pattern_str)
            # Replace non-named groups
            pattern_str = re.sub(r"\([^)]+\)", r"[variable]", pattern_str)
            # Replace Django's regex patterns with more readable ones
            pattern_str = pattern_str.replace("^", "").replace("$", "")
            # Add leading slash if needed
            if not pattern_str.startswith("/"):
                pattern_str = "/" + pattern_str
            url_patterns.append(pattern_str)

        elif isinstance(pattern, URLResolver):
            # If it's an include, recursively get patterns
            new_prefix = prefix + str(pattern.pattern)
            url_patterns.extend(get_all_urls(pattern, new_prefix))

    return url_patterns


def compile_pattern_regexes(patterns):
    """
    Pre-compile all pattern regexes for faster matching
    """
    compiled_patterns = []
    for pattern in patterns:
        # Escape special regex chars but keep our placeholder
        regex_pattern = re.escape(pattern).replace("\\[variable\\]", "[^/]+")
        # Ensure pattern matches full path
        regex_pattern = f"^{regex_pattern}$"
        compiled_patterns.append(re.compile(regex_pattern))
    return compiled_patterns


def normalize_path(path):
    """Normalize a URL path for comparison"""
    # Remove query parameters
    if "?" in path:
        path = path.split("?")[0]

    # Ensure leading slash
    if not path.startswith("/"):
        path = "/" + path

    return path


def is_valid_path(path, compiled_patterns, normalized_paths_cache, prefix_tree=None):
    """Check if a path matches any of the valid patterns."""
    # Use cache to avoid normalizing the same path multiple times
    if path in normalized_paths_cache:
        normalized_path = normalized_paths_cache[path]
    else:
        normalized_path = normalize_path(path.strip())
        normalized_paths_cache[path] = normalized_path

    # Quick check using prefix tree if available
    if prefix_tree and not path_matches_prefix_tree(normalized_path, prefix_tree):
        return False

    # Check against all compiled patterns
    return any(pattern.match(normalized_path) for pattern in compiled_patterns)


def build_prefix_tree(patterns):
    """
    Build a prefix tree (trie) for fast pattern matching
    This helps quickly eliminate paths that don't match any pattern
    """
    prefix_tree = {}
    for pattern in patterns:
        parts = pattern.strip("/").split("/")
        current = prefix_tree
        for part in parts:
            if part not in current:
                current[part] = {}
            current = current[part]
        # Mark the end of a valid pattern
        current["__END__"] = True
    return prefix_tree


def path_matches_prefix_tree(path, prefix_tree):
    """
    Quick check if a path could possibly match any pattern
    by checking if its prefix exists in the prefix tree
    """
    parts = path.strip("/").split("/")
    current = prefix_tree

    for i, part in enumerate(parts):
        # Check for variable part
        if part in current:
            current = current[part]
        # Check for exact match
        elif "[variable]" in current:
            current = current["[variable]"]
        # No match found
        else:
            return False

        # If we've reached a valid endpoint and consumed all parts
        if "__END__" in current and i == len(parts) - 1:
            return True

    return False


def debug_path(path, patterns):
    """Debug a specific path to see if it matches any patterns."""
    normalized_path = normalize_path(path)
    print(f"Debugging path: {path}")
    print(f"Normalized to: {normalized_path}")

    # Compile patterns
    compiled_patterns = compile_pattern_regexes(patterns)

    # Check matches
    matches = []
    for i, pattern in enumerate(patterns):
        if compiled_patterns[i].match(normalized_path):
            matches.append(pattern)

    if matches:
        print(f"Path matched {len(matches)} patterns:")
        for match in matches:
            print(f"  - {match}")
    else:
        print("Path didn't match any patterns.")
        # Find closest matching patterns
        closest = []
        for pattern in patterns:
            # Simple similarity check - count matching segments
            path_parts = normalized_path.strip("/").split("/")
            pattern_parts = pattern.strip("/").split("/")

            # Skip if different number of segments
            if len(path_parts) != len(pattern_parts):
                continue

            matches = sum(
                1 for pp, ptp in zip(path_parts, pattern_parts, strict=False) if pp == ptp or ptp == "[variable]"
            )

            if matches >= len(path_parts) - 1:
                closest.append((pattern, matches))

        if closest:
            closest.sort(key=lambda x: x[1], reverse=True)
            print("\nClosest matching patterns:")
            for pattern, score in closest[:5]:
                print(f"  - {pattern} (score: {score})")


def main():
    """Filter a CSV file for valid Open Chat Studio paths."""
    start_time = time.time()

    # Check for debug mode
    if len(sys.argv) == 3 and sys.argv[1] == "--debug":
        # Get all URL patterns
        all_patterns = get_all_urls()
        debug_path(sys.argv[2], all_patterns)
        return
    if len(sys.argv) == 2 and sys.argv[1] == "--show-paths":
        all_patterns = get_all_urls()
        print("All URL patterns:")
        for pattern in all_patterns:
            print(f"  - {pattern}")
        return

    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} input.csv output.csv")
        print(f"       {sys.argv[0]} --debug /path/to/debug")
        print(f"       {sys.argv[0]} --show-paths")
        sys.exit(1)

    input_file = Path(sys.argv[1])
    output_file = Path(sys.argv[2])

    if not input_file.exists():
        print(f"Error: Input file {input_file} not found.")
        sys.exit(1)

    # Get all URL patterns from Django
    print("Getting URL patterns...")
    all_patterns = get_all_urls()

    # Filter out admin or API patterns if needed
    excluded_prefixes = ["/admin/", "/api/"]
    valid_patterns = [p for p in all_patterns if not any(p.startswith(ex) for ex in excluded_prefixes)]

    # Print some of the patterns for verification
    print(f"Found {len(valid_patterns)} URL patterns. Examples:")
    for pattern in valid_patterns[:10]:  # Only show first 10 patterns
        print(f"  - {pattern}")

    # Precompile all patterns for faster matching
    print("Compiling patterns...")
    compiled_patterns = compile_pattern_regexes(valid_patterns)

    # Build prefix tree for fast filtering
    print("Building prefix tree...")
    prefix_tree = build_prefix_tree(valid_patterns)

    # Cache for normalized paths
    normalized_paths_cache = {}

    valid_count = 0
    total_count = 0

    print("Processing CSV file...")
    try:
        with open(input_file, newline="") as infile, open(output_file, "w", newline="") as outfile:
            reader = csv.reader(infile)
            writer = csv.writer(outfile)

            # Assume first row is header and find URL path column
            header = next(reader)
            writer.writerow(header)  # Copy header to output

            path_column = None
            for i, col_name in enumerate(header):
                if col_name.lower() in ["path", "url", "url_path", "pathname", "route"]:
                    path_column = i
                    break

            # If path column not found, assume first column
            if path_column is None:
                path_column = 0
                print("Warning: Could not identify path column, using first column.")

            # Process in batches for better performance
            batch_size = 1000
            rows_batch = []

            for row in reader:
                total_count += 1

                if len(row) > path_column:
                    path = row[path_column]
                    if is_valid_path(path, compiled_patterns, normalized_paths_cache, prefix_tree):
                        rows_batch.append(row)
                        valid_count += 1

                # Write batch to file
                if len(rows_batch) >= batch_size:
                    writer.writerows(rows_batch)
                    rows_batch = []

                # Progress reporting
                if total_count % 10000 == 0:
                    print(f"Processed {total_count} rows so far, found {valid_count} valid paths...")

            # Write any remaining rows
            if rows_batch:
                writer.writerows(rows_batch)

    except Exception as e:
        print(f"Error processing file: {e}")
        sys.exit(1)

    elapsed_time = time.time() - start_time
    print(f"Processed {total_count} rows, found {valid_count} valid paths.")
    print(f"Results written to {output_file}")
    print(f"Total time: {elapsed_time:.2f} seconds")


if __name__ == "__main__":
    main()
