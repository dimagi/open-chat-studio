# How to Prepare a Good Pull Request

Open Chat Studio is an open-source project, and you can contribute to its code directly. To do so, follow these guidelines for creating Pull Requests (PRs) to maximize the chances of your changes being merged.

## General Rules for a Good Pull Request

* Fork the repository and use your fork to create PRs. Avoid creating change branches in the main repository.
* Choose an appropriate branch for your work and create your own branch based on it.
* Give your branches, commits, and Pull Requests meaningful names and descriptions. This helps track changes later. If your changes cover a particular component, indicate it in the PR name as a prefix, for example: `[DOCS] PR name`.
* Keep your PRs small—each PR should address one issue. Remove all unrelated changes.
* [Link your Pull Request to an issue](https://docs.github.com/en/issues/tracking-your-work-with-issues/linking-a-pull-request-to-an-issue-using-the-pull-request-sidebar) if applicable.
* Document your contribution! If your changes impact how users interact with Open Chat Studio, update the relevant documentation. You can do this yourself or collaborate with documentation contributors.
* For Work In Progress or early test results, use a Draft PR.

## Ensure Change Quality

Your pull request will automatically be tested and marked as "green" when it is ready for merging. If any builds fail ("red" status), you need to fix the issues listed in console logs. Any change to the PR branch will automatically trigger the checks, so you don't need to recreate the PR—just wait for the updated results.

Regardless of automated tests, ensure the quality of your changes:

* Test your changes locally:
  * Double-check your code.
  * Run tests locally to identify and fix potential issues.
* Before creating a PR, ensure your branch is up to date with the latest state of the branch you are contributing to (e.g. `git fetch upstream && git merge upstream/master`).

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

* We use `pytest` for unit testing.
* All changes should ideally include tests.
* Focus on testing business logic and complex functionality.
* Generally, we don't test Django views unless they contain significant logic.
  * When view logic becomes complex, extract it into separate functions and test those.

## Documentation Policy

* User-facing changes should be accompanied by documentation updates in the [docs repo](https://github.com/dimagi/open-chat-studio-docs/).
* Link the docs PR to the code PR.
* Merge the docs PR after the code PR.

See the [user docs guide](../developer_guides/user_docs.md) for more detail.

## Communication

We use GitHub issues for most work and have a GitHub project where we prioritize and plan work: [GitHub Project](https://github.com/orgs/dimagi/projects/3/views/1).

General questions and discussions can be conducted in the [GitHub Discussions](https://github.com/dimagi/open-chat-studio/discussions) section.

## Need Additional Help? Check These Articles

* [How to create a fork](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/working-with-forks/fork-a-repo)
* [Install Git](https://git-scm.com/book/en/v2/Getting-Started-First-Time-Git-Setup)
