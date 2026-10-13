import { execFileSync } from "node:child_process";
import path from "node:path";

import { E2E_BASE_URL } from "./config";
import { E2E_SCENARIOS_ENV, SCENARIOS } from "./scenarios";

const repoRoot = path.resolve(__dirname, "..");

export default async function globalSetup() {
  // The command deletes the previous run's data, then creates a user and team for each scenario.
  const args = ["run", "python", "manage.py", "e2e_seed", "--base-url", E2E_BASE_URL];
  for (const [name, state] of Object.entries(SCENARIOS)) {
    args.push("--scenario", `${name}=${state}`);
  }
  const output = execFileSync("uv", args, { cwd: repoRoot, encoding: "utf8", stdio: ["ignore", "pipe", "inherit"] });
  try {
    JSON.parse(output);
  } catch {
    throw new Error(`e2e_seed printed something other than JSON:\n${output}`);
  }
  process.env[E2E_SCENARIOS_ENV] = output;
}
