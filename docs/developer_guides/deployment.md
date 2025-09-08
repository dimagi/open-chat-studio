# Deployment Process

### Continuous Deployment

- All changes merged to the `main` branch trigger an automated deployment.
- Deployment is only triggered by successful completion of the [lint_and_test.yml](https://github.com/dimagi/open-chat-studio/blob/main/.github/workflows/lint_and_test.yml){target="_blank"} GitHub workflow.
- Deployment is executed by the [deploy.yml](https://github.com/dimagi/open-chat-studio/blob/main/.github/workflows/deploy.yml){target="_blank"} GitHub action.

### Monitoring Deployments

- Deploy notifications are automatically sent to #open-chat-studio-dev Slack channel.
- Each notification includes:
    - Build status
    - Changes included in the deployment
    - Deploy completion status

### Best Practices
- Always monitor the Slack channels after merging to main.
- Watch for successful completion of your deployment.
- Watch for Sentry errors after deployment.

### Rollback Process
- Although rollback is possible, we would prefer to roll forward by deploying a fix to the issue.
- If issues are detected, notify the team in #open-chat-studio-dev.
- Monitor \#ocs-ops for any related errors.
- Work with the team to determine the best course of action.
