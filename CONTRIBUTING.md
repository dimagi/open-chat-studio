# Contributing to Open Chat Studio

Thanks for your interest in contributing to Open Chat Studio! This document outlines the process for contributing to the project.

## **Did you find a bug?**

* **Ensure the bug was not already reported** by searching on GitHub under [Issues](https://github.com/dimagi/open-chat-studio/issues).

* If you're unable to find an open issue addressing the problem, [open a new one](https://github.com/dimagi/open-chat-studio/issues/new). Be sure to include a **title and clear description**, as much relevant information as possible.


## Development Setup

1. Fork and clone the repository
2. Follow the setup instructions in the [README](README.md)
3. Install Docker if not already installed

## Development Process

### Branch Naming
Use the format `{initial}/branch-name` for all branches. For example:
- `jd/add-user-auth`
- `ms/fix-api-response`
- `ak/update-docs`

### Making Changes
1. Create a new branch following the naming convention
2. Make your changes
3. Write tests for new functionality
4. Ensure all tests pass by running `pytest`

### Pull Request Process
1. Create your PR in draft state
2. Add a clear description of your changes and link any related issues
3. Request AI review by commenting "@coderabbit review" on your PR
4. Address any AI-suggested improvements
5. When ready, change PR state to "Ready for review" and assign reviewers
6. Address reviewer feedback
7. Once approved, your changes can be merged

Note: The AI review stage is optional but recommended for larger changes.

### Testing
- We use pytest for unit testing
- All changes should ideally include tests
- Focus on testing business logic and complex functionality
- Generally, we don't test Django views unless they contain significant logic
  - When view logic becomes complex, extract it into separate functions and test those

## Communication

We use GitHub issues for most work and have a GitHub project where we prioritize and plan work: https://github.com/orgs/dimagi/projects/3/views/1

General questions and discussions can be done in the [GitHub Discussions](https://github.com/dimagi/open-chat-studio/discussions) section. 
