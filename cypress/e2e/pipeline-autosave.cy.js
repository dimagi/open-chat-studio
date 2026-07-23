/**
 * UI reproduction for issue #3895 — the false "This pipeline was modified in
 * another session" conflict that appeared when making quick successive edits to
 * a (typically large) pipeline.
 *
 * Root cause was client-side: the debounced autosave in `pipelineStore.ts`
 * derived `base_revision` from `currentRevision`, which is only refreshed once a
 * PATCH *response* lands. With no in-flight guard, a second edit fired a PATCH
 * carrying the stale revision, which the server correctly rejected with 409 and
 * the client surfaced as a cross-session conflict.
 *
 * This spec drives the real editor: it delays the first PATCH response (so it is
 * still in-flight when the second edit's debounce fires) and asserts that
 *   1. no conflict banner appears, and
 *   2. saves are serialized — the second PATCH departs with the freshly-bumped
 *      `base_revision`, not the stale one.
 *
 * Requires the standard Cypress environment (running server + a team that owns
 * at least one pipeline). Configure TEAM_SLUG / TEST_USER / TEST_PASSWORD in
 * cypress.env.json.
 */
describe("Pipeline autosave race (issue #3895)", () => {
  const teamSlug = Cypress.env("TEAM_SLUG") || "your-team-slug";

  beforeEach(() => {
    cy.login();
    // Open the first available pipeline in the editor.
    cy.visit(`/a/${teamSlug}/pipelines/`);
    cy.get("table tbody tr", { timeout: 10000 }).should("exist");
    cy.get('table tbody tr a[href*="/pipelines/"]').first().click({ force: true });
    cy.url().should("match", /pipelines\/\d+\/edit\//, { timeout: 10000 });
    // Wait for React Flow to render the graph.
    cy.get(".react-flow__node", { timeout: 15000 }).should("exist");
  });

  // Nudge a node by (dx, dy); each drag mutates the graph and triggers an autosave.
  const dragNodeBy = (index, dx, dy) => {
    cy.get(".react-flow__node")
      .eq(index)
      .then(($node) => {
        const rect = $node[0].getBoundingClientRect();
        const startX = rect.x + rect.width / 2;
        const startY = rect.y + rect.height / 2;
        cy.wrap($node).trigger("mousedown", {
          button: 0,
          clientX: startX,
          clientY: startY,
          force: true,
        });
        cy.get(".react-flow__pane")
          .trigger("mousemove", { clientX: startX + dx, clientY: startY + dy, force: true })
          .trigger("mouseup", { force: true });
      });
  };

  it("serializes rapid saves without a false cross-session conflict", () => {
    const baseRevisions = [];
    let patchCount = 0;

    cy.intercept("PATCH", "**/pipelines/data/*", (req) => {
      patchCount += 1;
      baseRevisions.push(req.body.base_revision);
      // Hold the first response open so it is still in-flight when the second
      // edit's debounce fires — this is the exact timing that produced #3895.
      if (patchCount === 1) {
        req.on("response", (res) => res.setDelay(3000));
      }
    }).as("patch");

    // Two quick edits. Autosave debounces at 1s, so the second edit's PATCH is
    // scheduled while the (artificially delayed) first PATCH is still in-flight.
    dragNodeBy(0, 60, 40);
    cy.wait(1200); // let the first autosave fire
    dragNodeBy(0, -40, 30);

    // Both PATCHes must eventually complete.
    cy.wait("@patch");
    cy.wait("@patch");

    // The false-conflict banner must never appear.
    cy.contains(/modified in another session/i, { timeout: 6000 }).should("not.exist");
    cy.get('[role="alert"]').contains(/another session/i).should("not.exist");

    // Saves were serialized: the second PATCH used the revision the first
    // established, not the stale baseline it started from.
    cy.then(() => {
      expect(baseRevisions.length).to.be.gte(2);
      expect(baseRevisions[1]).to.be.greaterThan(baseRevisions[0]);
    });
  });
});
