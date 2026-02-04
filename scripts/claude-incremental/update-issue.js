#!/usr/bin/env node

/**
 * Update a GitHub issue after Claude completes a task.
 *
 * Actions:
 * - Check off completed task item
 * - Add/update learnings section
 * - Add comment summarizing work done
 * - Optionally mark item as blocked
 *
 * Usage:
 *   node update-issue.js --owner OWNER --repo REPO --issue NUMBER --task-index N --action complete [--learnings "..."] [--comment "..."]
 *   node update-issue.js --owner OWNER --repo REPO --issue NUMBER --task-index N --action block --reason "..."
 *
 * Environment:
 *   GITHUB_TOKEN - GitHub token with issues write permission
 */

const https = require("https");

/**
 * Make a GitHub API request
 * @param {string} method - HTTP method
 * @param {string} path - API path (e.g., /repos/owner/repo/issues/1)
 * @param {Object} body - Request body (for POST/PATCH)
 * @returns {Promise<Object>} Response data
 */
function githubRequest(method, path, body = null) {
  const token = process.env.GITHUB_TOKEN;
  if (!token) {
    throw new Error("GITHUB_TOKEN environment variable required");
  }

  return new Promise((resolve, reject) => {
    const options = {
      hostname: "api.github.com",
      port: 443,
      path: path,
      method: method,
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "claude-incremental-worker",
        "X-GitHub-Api-Version": "2022-11-28",
      },
    };

    if (body) {
      options.headers["Content-Type"] = "application/json";
    }

    const req = https.request(options, (res) => {
      let data = "";
      res.on("data", (chunk) => (data += chunk));
      res.on("end", () => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(data ? JSON.parse(data) : {});
        } else {
          reject(
            new Error(`GitHub API error ${res.statusCode}: ${data}`)
          );
        }
      });
    });

    req.on("error", reject);

    if (body) {
      req.write(JSON.stringify(body));
    }
    req.end();
  });
}

/**
 * Check off a task in the issue body
 * @param {string} body - Issue body
 * @param {number} taskIndex - 0-based index of task to check off
 * @returns {string} Updated body
 */
function checkOffTask(body, taskIndex) {
  const lines = body.split("\n");
  let taskCount = 0;

  for (let i = 0; i < lines.length; i++) {
    // Match unchecked task lines
    if (lines[i].match(/^-\s*\[ \]/)) {
      if (taskCount === taskIndex) {
        lines[i] = lines[i].replace(/^(-\s*)\[ \]/, "$1[x]");
        break;
      }
      taskCount++;
    }
  }

  return lines.join("\n");
}

/**
 * Mark a task as blocked in the issue body
 * @param {string} body - Issue body
 * @param {number} taskIndex - 0-based index of task to mark blocked
 * @param {string} reason - Reason for blocking
 * @returns {string} Updated body
 */
function markTaskBlocked(body, taskIndex, reason) {
  const lines = body.split("\n");
  let taskCount = 0;

  for (let i = 0; i < lines.length; i++) {
    // Match unchecked, non-blocked task lines
    if (lines[i].match(/^-\s*\[ \]/) && !lines[i].includes("blocked:")) {
      if (taskCount === taskIndex) {
        // Insert "blocked:" after the checkbox, with optional reason
        const reasonSuffix = reason ? ` (${reason})` : "";
        lines[i] = lines[i].replace(
          /^(-\s*\[ \]\s*)(.+)$/,
          `$1blocked: $2${reasonSuffix}`
        );
        break;
      }
      taskCount++;
    }
  }

  return lines.join("\n");
}

/**
 * Add or update the Learnings section
 * @param {string} body - Issue body
 * @param {string} newLearnings - New learnings to add (can be multiple lines)
 * @returns {string} Updated body
 */
function updateLearnings(body, newLearnings) {
  if (!newLearnings || !newLearnings.trim()) return body;

  // Format learnings as bullet points if not already
  const learningLines = newLearnings
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line)
    .map((line) => (line.startsWith("-") ? line : `- ${line}`))
    .join("\n");

  // Split body by ## headings to find Learnings section
  const sections = body.split(/(\n(?=##\s))/);

  let learningsSectionIndex = -1;
  for (let i = 0; i < sections.length; i++) {
    if (/^##\s*Learnings/i.test(sections[i])) {
      learningsSectionIndex = i;
      break;
    }
  }

  if (learningsSectionIndex >= 0) {
    // Append to existing section
    sections[learningsSectionIndex] =
      sections[learningsSectionIndex].trimEnd() + "\n" + learningLines;
    return sections.join("");
  } else {
    // Add new Learnings section at the end
    return body.trimEnd() + "\n\n## Learnings\n" + learningLines + "\n";
  }
}

/**
 * Parse command line arguments
 * @returns {Object} Parsed arguments
 */
function parseArgs() {
  const args = process.argv.slice(2);
  const result = {};

  for (let i = 0; i < args.length; i++) {
    switch (args[i]) {
      case "--owner":
        result.owner = args[++i];
        break;
      case "--repo":
        result.repo = args[++i];
        break;
      case "--issue":
        result.issueNumber = parseInt(args[++i], 10);
        break;
      case "--task-index":
        result.taskIndex = parseInt(args[++i], 10);
        break;
      case "--action":
        result.action = args[++i];
        break;
      case "--learnings":
        result.learnings = args[++i];
        break;
      case "--comment":
        result.comment = args[++i];
        break;
      case "--reason":
        result.reason = args[++i];
        break;
      case "--pr-url":
        result.prUrl = args[++i];
        break;
    }
  }

  return result;
}

async function main() {
  const args = parseArgs();

  // Validate required arguments
  if (!args.owner || !args.repo || !args.issueNumber || args.taskIndex === undefined || !args.action) {
    console.error("Required arguments: --owner, --repo, --issue, --task-index, --action");
    console.error("Actions: complete, block");
    console.error("Optional: --learnings, --comment, --reason (for block), --pr-url");
    process.exit(1);
  }

  const basePath = `/repos/${args.owner}/${args.repo}/issues/${args.issueNumber}`;

  // Fetch current issue
  console.log(`Fetching issue #${args.issueNumber}...`);
  const issue = await githubRequest("GET", basePath);

  let updatedBody = issue.body;

  // Apply action
  if (args.action === "complete") {
    updatedBody = checkOffTask(updatedBody, args.taskIndex);
    console.log(`Checked off task at index ${args.taskIndex}`);
  } else if (args.action === "block") {
    updatedBody = markTaskBlocked(updatedBody, args.taskIndex, args.reason || "");
    console.log(`Marked task at index ${args.taskIndex} as blocked`);
  } else {
    console.error(`Unknown action: ${args.action}`);
    process.exit(1);
  }

  // Add learnings if provided
  if (args.learnings) {
    updatedBody = updateLearnings(updatedBody, args.learnings);
    console.log("Updated learnings section");
  }

  // Update issue body
  console.log("Updating issue body...");
  await githubRequest("PATCH", basePath, { body: updatedBody });

  // Add comment if provided
  if (args.comment) {
    const commentBody = args.prUrl
      ? `${args.comment}\n\nPR: ${args.prUrl}`
      : args.comment;

    console.log("Adding comment...");
    await githubRequest("POST", `${basePath}/comments`, {
      body: commentBody,
    });
  }

  console.log("Done!");
}

// Main execution
if (require.main === module) {
  main().catch((err) => {
    console.error("Error:", err.message);
    process.exit(1);
  });
}

module.exports = { checkOffTask, markTaskBlocked, updateLearnings };
