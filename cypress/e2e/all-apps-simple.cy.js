describe('All Application Pages', () => {
  const teamSlug = Cypress.env('TEAM_SLUG') || 'your-team-slug'

  beforeEach(() => {
    cy.login()
  })

  describe('Dashboard', () => {
    it('dashboard page loads', () => {
      cy.visit(`/a/${teamSlug}/dashboard/`)
      cy.pageTitleEquals('Team Dashboard')
    })

    it('dashboard has charts or data', () => {
      cy.visit(`/a/${teamSlug}/dashboard/`)
      cy.get('canvas').its('length')
        .then((count) => {
          expect(count).to.be.greaterThan(2);
        });
    })
  })

  describe('Assistants', () => {
    it('assistants page loads', () => {
      cy.visit(`/a/${teamSlug}/assistants/`)
      cy.pageTitleEquals('OpenAI Assistant')
    })

    it('assistants page has table and new button', () => {
      cy.visit(`/a/${teamSlug}/assistants/`)
      cy.get('table').should('exist')
      cy.get('[data-cy="btn-new"]').should('be.visible')
        .and('contain.text', 'Add new')
    })

    it('can access new assistant page', () => {
      cy.visit(`/a/${teamSlug}/assistants/`)
      cy.get('[data-cy="btn-new"]').click()
      cy.pageTitleEquals('Create OpenAI Assistant')
      cy.get('input[name="name"]').should('be.visible')
    })
  })

  describe('Files', () => {
    it('files page loads', () => {
      cy.visit(`/a/${teamSlug}/files/file`)
      cy.pageTitleEquals('Files')
      cy.get('table').should('exist')
    })
  })

  describe('Collections', () => {
    it('collections page loads', () => {
      cy.visit(`/a/${teamSlug}/documents/collection/`)
      cy.pageTitleEquals('Collections')
      cy.get('table').should('exist')
      cy.get('[data-cy="btn-new"]').should('be.visible')
        .and('contain.text', 'Add new')
    })

    it('can access new collections page', () => {
      cy.visit(`/a/${teamSlug}/documents/collection/`)
      cy.get('[data-cy="btn-new"]').click()
      cy.pageTitleEquals('Create Collection')
      cy.get('input[name="name"]').should('be.visible')
    })
  })

  describe('Team Settings', () => {
    it('manage team page loads', () => {
      cy.visit(`/a/${teamSlug}/team/`)
      cy.pageTitleEquals('Team Details')
      cy.get('input[name="name"]').should('be.visible').and('have.value', 'Test Team')
    })

    const serviceProviderTypes = [
      'llm',
      'voice',
      'messaging',
      'auth',
      'actions',
      // 'mcp',  feature flag
      'tracing'
    ]
    serviceProviderTypes.forEach(type => {
      it(`manage team page contains '${type}' section`, () => {
        cy.visit(`/a/${teamSlug}/team/`)
        cy.get(`[data-cy="title-${type}"]`).should('exist')
      })
    })
  })

  describe('Navigation', () => {
    it(`should show left side navigation`, () => {
      cy.viewport(1024, 768)
      cy.visit(`/a/${teamSlug}/chatbots/`)
      cy.get(`[data-cy="btn-team-nav"]`).click()
      cy.get(`[data-cy="nav-team-settings"]`).should('be.visible')
      cy.get(`[data-cy="nav-add-team"]`).should('be.visible')
      cy.get(`[data-cy="nav-docs"]`).should('be.visible')
    })

    it(`should show mobile navigation`, () => {
      cy.viewport(768, 1024)
      cy.visit(`/a/${teamSlug}/chatbots/`)
      cy.get(`[data-cy="btn-team-nav-mobile"]`).click()
      cy.get(`[data-cy="nav-team-settings"]`).should('be.visible')
      cy.get(`[data-cy="nav-add-team"]`).should('be.visible')
      cy.get(`[data-cy="nav-docs"]`).should('be.visible')
    })
  })
})
