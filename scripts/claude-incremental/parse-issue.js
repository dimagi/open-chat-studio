#!/usr/bin/env node

/**
 * Parse a GitHub issue body to extract structured sections for the Claude Incremental Worker.
 *
 * Extracts:
 * - Goal (from ## Goal section)
 * - Tasks (from ## Tasks checklist)
 * - Context (from ## Context section)
 * - Learnings (from ## Learnings section)
 *
 * Usage:
 *   node parse-issue.js <issue-body-file>
 *   echo "<issue body>" | node parse-issue.js
 *
 * Output: JSON object with extracted sections
 */

const fs = require("fs");

/**
 * Parse a checklist item into structured data
 * @param {string} line - A line like "- [ ] Convert foo.js" or "- [x] Done task" or "- [ ] blocked: Can't do"
 * @returns {Object|null} Parsed task or null if not a task line
 */
function parseTaskLine(line) {
  // Match: - [ ] or - [x] followed by optional "blocked:" and task text
  const match = line.match(/^-\s*\[([ xX])\]\s*(blocked:\s*)?(.+)$/);
  if (!match) return null;

  const [, checkmark, blockedPrefix, text] = match;
  return {
    completed: checkmark.toLowerCase() === "x",
    blocked: Boolean(blockedPrefix),
    text: text.trim(),
    raw: line,
  };
}

/**
 * Extract a section's content from markdown
 * @param {string} body - Full issue body
 * @param {string} sectionName - Section header name (e.g., "Goal")
 * @returns {string} Section content (trimmed) or empty string
 */
function extractSection(body, sectionName) {
  // Split body by ## headings, keeping the heading with each section
  const sections = body.split(/\n(?=##\s)/);

  // Find the section with matching name
  const sectionRegex = new RegExp(`^##\\s*${sectionName}\\s*\\n`, "i");

  for (const section of sections) {
    if (sectionRegex.test(section)) {
      // Remove the heading line and return the content
      let content = section.replace(sectionRegex, "");
      // Clean up: remove HTML comments and trim
      content = content.replace(/<!--[\s\S]*?-->/g, "").trim();
      return content;
    }
  }

  return "";
}

/**
 * Parse the Tasks section into structured task list
 * @param {string} tasksContent - Content of the ## Tasks section
 * @returns {Array} Array of task objects
 */
function parseTasks(tasksContent) {
  if (!tasksContent) return [];

  const lines = tasksContent.split("\n");
  const tasks = [];

  for (const line of lines) {
    const task = parseTaskLine(line.trim());
    if (task) {
      tasks.push(task);
    }
  }

  return tasks;
}

/**
 * Parse an issue body into structured data
 * @param {string} body - The issue body markdown
 * @returns {Object} Parsed issue data
 */
function parseIssue(body) {
  const goal = extractSection(body, "Goal");
  const tasksContent = extractSection(body, "Tasks");
  const context = extractSection(body, "Context");
  const learnings = extractSection(body, "Learnings");

  const tasks = parseTasks(tasksContent);

  // Compute summary stats
  const completedCount = tasks.filter((t) => t.completed).length;
  const blockedCount = tasks.filter((t) => !t.completed && t.blocked).length;
  const pendingCount = tasks.filter((t) => !t.completed && !t.blocked).length;

  return {
    goal,
    tasks,
    context,
    learnings,
    stats: {
      total: tasks.length,
      completed: completedCount,
      blocked: blockedCount,
      pending: pendingCount,
    },
  };
}

// Main execution
if (require.main === module) {
  let input = "";

  // Read from file argument or stdin
  if (process.argv[2]) {
    input = fs.readFileSync(process.argv[2], "utf8");
  } else if (!process.stdin.isTTY) {
    input = fs.readFileSync(0, "utf8"); // Read from stdin
  } else {
    console.error("Usage: node parse-issue.js <issue-body-file>");
    console.error("   or: echo '<body>' | node parse-issue.js");
    process.exit(1);
  }

  const result = parseIssue(input);
  console.log(JSON.stringify(result, null, 2));
}

module.exports = { parseIssue, parseTaskLine, extractSection, parseTasks };
