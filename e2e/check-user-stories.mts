/**
 * Checks that docs/core_user_stories.md and the Playwright tests agree.
 *
 * Reads each test's `user-stories` annotations from Playwright itself (`playwright test --list`),
 * so only annotations on tests Playwright would actually load count. Skipped tests do not count. Fails when:
 * - a story has no "Covered by" entry, or lists a spec file that does not exist
 * - a listed spec file has no runnable (non-skipped) test annotated with that story's number
 * - a test is annotated with a story number that is not in the doc, or with a malformed description
 */
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const REPO_ROOT = path.resolve(import.meta.dirname, "..");
const STORIES_DOC = path.join(REPO_ROOT, "docs", "core_user_stories.md");
const STORY_ROW = /^\|\s*US-(\d+)\s*\|[^|]*\|([^|]*)\|\s*$/;
const ANNOTATION_TYPE = "user-stories";

type Annotation = { type: string; description?: string };
type JsonSpec = { title: string; file: string; tests: { annotations: Annotation[]; expectedStatus: string }[] };
type JsonSuite = { specs?: JsonSpec[]; suites?: JsonSuite[] };
type JsonReport = {
  config: { projects: { testDir: string }[] };
  suites: JsonSuite[];
  errors: { message: string }[];
};
type AnnotatedTest = { title: string; file: string; stories: Set<number>; skipped: boolean };

function parseStories(errors: string[]): Map<number, string[]> {
  const stories = new Map<number, string[]>();
  for (const line of readFileSync(STORIES_DOC, "utf8").split("\n")) {
    const match = STORY_ROW.exec(line);
    if (!match) continue;
    const number = Number(match[1]);
    if (stories.has(number)) errors.push(`US-${number} appears more than once in the doc`);
    const specs = [...match[2].matchAll(/`([^`]+)`/g)].map((m) => path.resolve(REPO_ROOT, m[1]));
    stories.set(number, specs);
  }
  if (stories.size === 0) errors.push(`No user stories found in ${STORIES_DOC}`);
  return stories;
}

function listPlaywrightTests(): JsonReport {
  // Run the CLI directly: going through pnpm can print warnings to stdout ahead of the JSON.
  const cli = createRequire(import.meta.url).resolve("@playwright/test/cli");
  const result = spawnSync(
    process.execPath,
    [cli, "test", "--config", "e2e/playwright.config.ts", "--list", "--reporter=json"],
    { cwd: REPO_ROOT, encoding: "utf8" },
  );
  try {
    return JSON.parse(result.stdout) as JsonReport;
  } catch {
    throw new Error(
      `Could not list Playwright tests (exit ${result.status}):\n${result.stderr || result.stdout.slice(0, 2000)}`,
    );
  }
}

function collectTests(report: JsonReport, errors: string[]): AnnotatedTest[] {
  const testDir = report.config.projects[0].testDir;
  const tests: AnnotatedTest[] = [];
  const visit = (suite: JsonSuite) => {
    for (const spec of suite.specs ?? []) {
      const stories = new Set<number>();
      const annotations = spec.tests.flatMap((t) => t.annotations).filter((a) => a.type === ANNOTATION_TYPE);
      for (const annotation of annotations) {
        const parts = (annotation.description ?? "").split(",").map((p) => p.trim());
        if (!parts.every((p) => /^\d+$/.test(p))) {
          errors.push(`"${spec.title}" has a malformed ${ANNOTATION_TYPE} annotation: "${annotation.description}"`);
          continue;
        }
        parts.forEach((p) => stories.add(Number(p)));
      }
      const skipped = spec.tests.every((t) => t.expectedStatus === "skipped");
      tests.push({ title: spec.title, file: path.join(testDir, spec.file), stories, skipped });
    }
    (suite.suites ?? []).forEach(visit);
  };
  report.suites.forEach(visit);
  return tests;
}

function check(): string[] {
  const errors: string[] = [];
  const stories = parseStories(errors);
  const report = listPlaywrightTests();
  errors.push(...report.errors.map((e) => `Playwright could not load the tests: ${e.message}`));
  const tests = collectTests(report, errors);

  for (const [number, specs] of stories) {
    if (specs.length === 0) errors.push(`US-${number} has no "Covered by" entry`);
    for (const spec of specs) {
      const relative = path.relative(REPO_ROOT, spec);
      if (!existsSync(spec)) {
        errors.push(`US-${number} is covered by ${relative}, which does not exist`);
      } else if (!tests.some((t) => t.file === spec && t.stories.has(number) && !t.skipped)) {
        const skipped = tests.filter((t) => t.file === spec && t.stories.has(number)).map((t) => `"${t.title}"`);
        errors.push(
          skipped.length > 0
            ? `US-${number} is covered by ${relative}, but its only annotated tests are skipped: ${skipped.join(", ")}`
            : `US-${number} is covered by ${relative}, but no test in it is annotated with story ${number}`,
        );
      }
    }
  }
  for (const test of tests) {
    for (const number of test.stories) {
      if (!stories.has(number)) {
        errors.push(`"${test.title}" (${path.relative(REPO_ROOT, test.file)}) is annotated with US-${number}, which is not in the doc`);
      }
    }
  }
  return errors;
}

const errors = check();
if (errors.length > 0) {
  console.error(errors.map((e) => `✗ ${e}`).join("\n"));
  process.exit(1);
}
console.log("All user stories are covered by Playwright tests.");
