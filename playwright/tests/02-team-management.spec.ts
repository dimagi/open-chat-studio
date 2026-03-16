import { test, expect } from '@playwright/test';
import { login, setupPage, TEAM_SLUG, TEAM_URL } from '../helpers/common';

const FLAGS_URL = `/a/${TEAM_SLUG}/team/flags/`;

test.describe('Team Management', () => {
  test.beforeEach(async ({ page }) => {
    await setupPage(page);
    await login(page);
  });

  test('Edit team name', async ({ page }) => {
    await page.goto(TEAM_URL);
    await expect(page.getByRole('heading', { name: 'Team Details' })).toBeVisible();

    const teamNameInput = page.getByRole('textbox', { name: 'Team Name' });
    const originalName = await teamNameInput.inputValue();

    // Update the team name
    const updatedName = `${originalName} Updated`;
    await teamNameInput.fill(updatedName);
    await page.getByRole('button', { name: 'Save' }).click();

    // Verify the name was updated
    await expect(page).toHaveTitle(new RegExp(updatedName));
    await expect(page.getByText('Team details saved!')).toBeVisible();

    // Revert the team name back to the original
    await page.getByRole('textbox', { name: 'Team Name' }).fill(originalName);
    await page.getByRole('button', { name: 'Save' }).click();
    await expect(page).toHaveTitle(new RegExp(originalName));
    await expect(page.getByText('Team details saved!')).toBeVisible();
  });

  // test('Invite a team member', async ({ page }) => {
  //   await page.goto(TEAM_URL);
  //   await expect(page.getByRole('heading', { name: 'Invite Team Members' })).toBeVisible();

  //   const inviteEmail = `invite-${Date.now()}@example.com`;

  //   // Fill in the invite email
  //   await page.getByRole('textbox', { name: 'Email' }).fill(inviteEmail);

  //   // Select the Team Admin role
  //   await page.getByRole('checkbox', { name: 'Team Admin' }).check();

  //   // Send the invitation
  //   await page.getByRole('button', { name: 'Send Invitation', exact: true }).click();

  //   // Verify the invitation appears in the pending invitations list
  //   await expect(page.getByRole('heading', { name: 'Pending Invitations' })).toBeVisible();
  //   await expect(page.getByRole('cell', { name: inviteEmail })).toBeVisible();

  //   // Clean up: cancel the invitation
  //   const inviteRow = page.getByRole('row', { name: new RegExp(inviteEmail) });
  //   await inviteRow.getByRole('button', { name: 'Cancel Invitation' }).click();
  //   await expect(page.getByRole('cell', { name: inviteEmail })).not.toBeVisible();
  // });

  test('Manage feature flags', async ({ page }) => {
    await page.goto(TEAM_URL);

    // Click "Manage Feature Flags" link
    await page.getByRole('link', { name: 'Manage Feature Flags' }).click();
    await expect(page).toHaveURL(FLAGS_URL);
    await expect(page.getByRole('heading', { name: 'Feature Flags', exact: true })).toBeVisible();

    const eventsFlag = page.getByRole('checkbox', { name: /Enables event-driven triggers/ });
    const evaluationsFlag = page.getByRole('checkbox', { name: /Chatbot Evaluations/ });
    const humanAnnotationsFlag = page.getByRole('checkbox', { name: /Human annotation queues/ });

    // Record the initial state of each flag
    const eventsInitial = await eventsFlag.isChecked();
    const evaluationsInitial = await evaluationsFlag.isChecked();
    const humanAnnotationsInitial = await humanAnnotationsFlag.isChecked();

    // Toggle each flag to the opposite state
    if (eventsInitial) {
      await eventsFlag.uncheck();
    } else {
      await eventsFlag.check();
    }
    if (evaluationsInitial) {
      await evaluationsFlag.uncheck();
    } else {
      await evaluationsFlag.check();
    }
    if (humanAnnotationsInitial) {
      await humanAnnotationsFlag.uncheck();
    } else {
      await humanAnnotationsFlag.check();
    }

    // Save changes
    await page.getByRole('button', { name: 'Save Changes' }).click();
    await expect(page.getByText('Feature flags updated successfully.')).toBeVisible();

    // Verify the flags are in the toggled state
    await expect(eventsFlag).toBeChecked({ checked: !eventsInitial });
    await expect(evaluationsFlag).toBeChecked({ checked: !evaluationsInitial });
    await expect(humanAnnotationsFlag).toBeChecked({ checked: !humanAnnotationsInitial });

    // Revert the flags back to their original state
    if (eventsInitial) {
      await eventsFlag.check();
    } else {
      await eventsFlag.uncheck();
    }
    if (evaluationsInitial) {
      await evaluationsFlag.check();
    } else {
      await evaluationsFlag.uncheck();
    }
    if (humanAnnotationsInitial) {
      await humanAnnotationsFlag.check();
    } else {
      await humanAnnotationsFlag.uncheck();
    }

    await page.getByRole('button', { name: 'Save Changes' }).click();
    await expect(page.getByText('Feature flags updated successfully.')).toBeVisible();
  });

  test('Danger Zone has Delete Team button', async ({ page }) => {
    await page.goto(TEAM_URL);

    // Verify the Danger Zone section exists with the Delete Team button
    await expect(page.getByRole('heading', { name: 'Danger Zone' })).toBeVisible();
    await expect(page.getByText('Delete Team', { exact: true })).toBeVisible();

    // Do NOT click the delete button - just verify it exists
  });
});
