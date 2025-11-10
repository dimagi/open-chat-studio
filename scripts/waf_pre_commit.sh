#!/bin/bash
# Script used by pre-commit to verify that the `@waf_allow` decorator is the first decorator listed.

if ! command -v rg &> /dev/null; then
    echo "Error: ripgrep (rg) is not installed."
    echo "Install it with:"
    echo "  - macOS: brew install ripgrep"
    echo "  - Ubuntu/Debian: apt-get install ripgrep"
    echo "  - Other: https://github.com/BurntSushi/ripgrep#installation"
    exit 1
fi

! rg -U ".+\n@waf_allow\(" --type python -n .
