import { execFileSync } from "node:child_process";
import path from "node:path";

import { E2E_EMAIL, E2E_PASSWORD, E2E_TEAM_SLUG } from "./config";

export default function globalSetup() {
  execFileSync(
    "uv",
    [
      "run",
      "python",
      "manage.py",
      "setup_e2e_team",
      "--email",
      E2E_EMAIL,
      "--password",
      E2E_PASSWORD,
      "--team-slug",
      E2E_TEAM_SLUG,
    ],
    { cwd: path.resolve(__dirname, ".."), stdio: "inherit" },
  );
}
