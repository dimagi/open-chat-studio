# Contributing

Thank you for your interest in contributing to Open Chat Studio! This guide will help you understand the contribution process and code style conventions.

## Getting Started

Before contributing, please make sure you've set up your development environment according to the [Getting Started](../getting-started/index.md) guide.


## Forms of Contribution

### Provide Feedback

- **Report Bugs / Issues**  
  If you encounter any issues or unexpected behavior in Open Chat Studio or its components, you can [create a new issue](https://github.com/dimagi/open-chat-studio/issues) in the GitHub issue tracker.

- **Propose New Features / Improvements**  
  If you have a suggestion for improving Open Chat Studio or want to share your ideas, you can open a new [GitHub Discussion](https://github.com/dimagi/open-chat-studio/discussions). If your idea is well-defined, you can also create a [Feature Request Issue](https://github.com/dimagi/open-chat-studio/issues/new?labels=enhancement%2Cfeature&template=feature_request.yml).  
  Provide a detailed description, including use cases, benefits, and potential challenges. Even if your idea is not immediately prioritized, it may still be considered later or taken up by the community.

### Contribute Code Changes

- **Fix Bugs or Develop New Features**  
  If you want to help improve Open Chat Studio's codebase, choose an issue from the [GitHub Issue Tracker](https://github.com/dimagi/open-chat-studio/issues) and [create a Pull Request](#3-submit-a-pull-request-pr) addressing it. If you are new, check out the [Good First Issues](https://github.com/orgs/dimagi/projects/3/views/1?filterQuery=label%3A%22good+first+issue%22).
  
  Before starting, ensure that the change has not already been implemented. You can build Open Chat Studio using the latest `main` branch and confirm that the modification is still needed. If the feature is complex, discuss it first in the [GitHub Discussions](https://github.com/dimagi/open-chat-studio/discussions).

### Improve Documentation

- **Developer Documentation** needs improvement, and we welcome contributions.
- **User Documentation** is maintained in the [open-chat-studio-docs repository](https://github.com/dimagi/open-chat-studio-docs) and published at [docs.openchatstudio.com](https://docs.openchatstudio.com/).
- The easiest way to contribute to documentation is by reviewing and providing feedback. If you notice errors or opportunities for improvement, reach out to documentation contributors or create a Pull Request directly.

## Technical Guide

This section provides the necessary steps to set up your environment, build Open Chat Studio locally, and run tests.

### 1. Set Up Your Environment

Before contributing, please make sure you've set up your development environment according to the [Getting Started](../getting-started/index.md) guide.

### 2. Start Working on Your First Issue

To contribute, pick a task from the [Good First Issues board](https://github.com/orgs/dimagi/projects/3/views/1?filterQuery=label%3A%22good+first+issue%22). To be assigned to an issue, leave a comment with the `.take` command in the selected issue.

### 3. Submit a Pull Request (PR)

Follow our [Pull Request guidelines](./pull_requests.md).

## Agent support
If you are using an agent other than Claude, consider creating a symbolic link to the CLAUDE.md file, but for your agent. For instance, to create a symlink for Gemini, run

```bash
ln -s CLAUDE.md GEMINI.md
```

Be sure add the new "file" to .gitignore.

## Getting Help

If you have any questions or need assistance:
- Use [GitHub Discussions](https://github.com/dimagi/open-chat-studio/discussions) for general queries.
- Check existing issues or open a new one if necessary.
- Reach out to maintainers in GitHub if you need further guidance.

## License

By contributing to Open Chat Studio, you agree that your contributions will be licensed under the terms stated in the [LICENSE](https://github.com/dimagi/open-chat-studio/blob/master/LICENSE) file.
