#!/usr/bin/env node

/**
 * Find the next unchecked, non-blocked task from a parsed issue.
 *
 * Usage:
 *   node find-next-task.js <issue-body-file>
 *   echo "<issue body>" | node find-next-task.js
 *
 * Output: JSON object with next task or status
 *   - { found: true, index: N, task: {...} } - Next task found
 *   - { found: false, reason: "all_complete" } - All tasks done
 *   - { found: false, reason: "all_blocked" } - Remaining tasks are blocked
 *   - { found: false, reason: "no_tasks" } - No tasks in issue
 */

const fs = require("fs");
const { parseIssue } = require("./parse-issue");

/**
 * Find the first unchecked, non-blocked task
 * @param {Array} tasks - Array of task objects from parseIssue
 * @returns {Object} Result with found status and task/reason
 */
function findNextTask(tasks) {
  if (!tasks || tasks.length === 0) {
    return { found: false, reason: "no_tasks" };
  }

  // Find first task that is not completed and not blocked
  for (let i = 0; i < tasks.length; i++) {
    const task = tasks[i];
    if (!task.completed && !task.blocked) {
      return {
        found: true,
        index: i,
        task: task,
      };
    }
  }

  // No unchecked, non-blocked tasks found
  const allComplete = tasks.every((t) => t.completed);
  if (allComplete) {
    return { found: false, reason: "all_complete" };
  }

  // Some tasks remain but all are blocked
  return { found: false, reason: "all_blocked" };
}

/**
 * Generate a branch name for the task
 * @param {number} issueNumber - GitHub issue number
 * @param {string} taskText - Task description text
 * @returns {string} Branch name
 */
function generateBranchName(issueNumber, taskText) {
  // Convert task text to slug: lowercase, replace spaces with dashes, remove special chars
  const slug = taskText
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, "")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-")
    .substring(0, 40) // Limit length
    .replace(/-$/, ""); // Remove trailing dash

  return `claude-incremental/${issueNumber}-${slug}`;
}

// Main execution
if (require.main === module) {
  let input = "";
  let issueNumber = null;

  // Parse arguments
  const args = process.argv.slice(2);
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--issue-number" && args[i + 1]) {
      issueNumber = parseInt(args[i + 1], 10);
      i++;
    } else if (!args[i].startsWith("-")) {
      input = fs.readFileSync(args[i], "utf8");
    }
  }

  // Read from stdin if no file provided
  if (!input && !process.stdin.isTTY) {
    input = fs.readFileSync(0, "utf8");
  }

  if (!input) {
    console.error(
      "Usage: node find-next-task.js [--issue-number N] <issue-body-file>"
    );
    console.error("   or: echo '<body>' | node find-next-task.js --issue-number N");
    process.exit(1);
  }

  const parsed = parseIssue(input);
  const result = findNextTask(parsed.tasks);

  // Add branch name if task found and issue number provided
  if (result.found && issueNumber) {
    result.branchName = generateBranchName(issueNumber, result.task.text);
  }

  // Include goal and context for workflow use
  if (result.found) {
    result.goal = parsed.goal;
    result.context = parsed.context;
    result.learnings = parsed.learnings;
  }

  console.log(JSON.stringify(result, null, 2));

  // Exit with code 1 if no task found (useful for workflow conditionals)
  if (!result.found) {
    process.exit(1);
  }
}

module.exports = { findNextTask, generateBranchName };
