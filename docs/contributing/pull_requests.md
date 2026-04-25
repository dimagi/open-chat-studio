# How to Prepare a Good Pull Request

Open Chat Studio is an open-source project, and you can contribute to its code directly. To do so, follow these guidelines for creating Pull Requests (PRs) to maximize the chances of your changes being merged.

## General Rules for a Good Pull Request

* Fork the repository and create branches in your fork (never in the main repository). Base your branch on an appropriate default branch (e.g., `main`).
* Give your branches, commits, and Pull Requests meaningful names and descriptions. This helps track changes later. If your changes cover a particular component, indicate it in the PR name as a prefix, for example: `[DOCS] PR name`.
* Keep your PRs small—each PR should address one issue. Remove all unrelated changes.
* [Link your Pull Request to an issue](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue) if applicable.
* [Document your contribution](#documentation-policy) if your changes are user-facing. [AI automation](../developer_guides/user_docs.md#user-documentation-process) can help draft documentation and changelog entries.
* For work in progress or early test results, use a Draft PR.

## Ensure Change Quality

Your pull request will automatically be tested. Changes to the PR branch trigger new checks, so you don't need to recreate the PR if tests fail—just fix the issues and push updates.

Regardless of automated tests, ensure the quality of your changes:

* [Test](#test-policy) your changes locally:
  * Double-check your code.
  * Run tests locally to identify and fix potential issues.
* Before creating a PR, ensure your branch is up to date with the base branch (e.g. `git fetch upstream && git merge upstream/main`).

## Pull Request Process

1. Create your PR in draft state.
2. Add a clear description of your changes and link any related issues.
3. Request AI review by commenting `@coderabbit review` on your PR.
4. Address any AI-suggested improvements.
5. When ready, change PR state to "Ready for review" and assign reviewers.
6. Address reviewer feedback.
7. Once approved, your changes can be merged.

**Note:** The AI review stage is optional but recommended for larger changes.

## Test Policy

* We use `pytest` for unit testing. Run tests locally with `uv run pytest`.
* All changes should ideally include tests.
* Focus on testing business logic and complex functionality.
* Generally, we don't test Django views unless they contain significant logic.
  * When view logic becomes complex, extract it into separate functions and test those.

## Documentation Policy

* **User-facing changes** follow these [guidelines](../developer_guides/user_docs.md)
* **API changes**: If your changes affect the REST API schema, update the `api-schema.yml` file. See the [API Documentation guide](../developer_guides/api_documentation.md) for details.

## Communication

* We use GitHub issues for most work and have a GitHub project where we prioritize and plan work: [GitHub Project](https://github.com/orgs/dimagi/projects/3/views/1).
* General questions and discussions can be posted in the [GitHub Discussions](https://github.com/dimagi/open-chat-studio/discussions) section.

## Additional Resources

* [How to create a fork](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/working-with-forks/fork-a-repo)
* [Linking a PR to an issue](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue)
* [Git setup guide](https://git-scm.com/book/en/v2/Getting-Started-First-Time-Git-Setup)
