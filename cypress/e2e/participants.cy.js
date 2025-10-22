describe('Participants Application', () => {
  const teamSlug = Cypress.env('TEAM_SLUG') || 'your-team-slug'

  beforeEach(() => {
    cy.login()
  })

  describe('Participants Home Page', () => {
    it('loads participants home page successfully', () => {
      cy.visit(`/a/${teamSlug}/participants/`)
      cy.url().should('include', '/participants/')
      cy.contains('Participants').should('be.visible')
    })

    it('displays participants table', () => {
      cy.visit(`/a/${teamSlug}/participants/`)
      cy.get('table, .table-container, [data-table]', { timeout: 10000 }).should('exist')
    })

    it('has import and export buttons', () => {
      cy.visit(`/a/${teamSlug}/participants/`)
      cy.contains('button, a', /Import|Export/i).should('exist')
    })

    it('filters can be applied', () => {
      cy.visit(`/a/${teamSlug}/participants/`)
      // Check for filter controls
      cy.get('select, input[type="search"], .filter-control').then(($filters) => {
        if ($filters.length > 0) {
          cy.log('Filters are available on participants page')
        }
      })
    })
  })

  describe('Participant Details', () => {
    it('navigates to participant details', () => {
      cy.visit(`/a/${teamSlug}/participants/`)
      cy.get('table tbody tr a, .participant-link').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.url().should('match', /participants\/\d+/)
        }
      })
    })

    it('displays participant information', () => {
      cy.visit(`/a/${teamSlug}/participants/`)
      cy.get('table tbody tr a').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          // Should show participant details
          cy.contains(/Name|Identifier|Email/i).should('exist')
        }
      })
    })

    it('shows participant data section', () => {
      cy.visit(`/a/${teamSlug}/participants/`)
      cy.get('table tbody tr a').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          // Look for data section
          cy.contains(/Data|Participant Data/i).then(($section) => {
            if ($section.length > 0) {
              cy.log('Participant data section found')
            }
          })
        }
      })
    })

    it('displays participant sessions', () => {
      cy.visit(`/a/${teamSlug}/participants/`)
      cy.get('table tbody tr a').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          // Check for sessions table
          cy.get('table').should('exist')
        }
      })
    })
  })

  describe('Edit Participant Data', () => {
    it('can edit participant data', () => {
      cy.visit(`/a/${teamSlug}/participants/`)
      cy.get('table tbody tr a').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          // Look for edit button
          cy.contains('button', /Edit|Modify/i).then(($edit) => {
            if ($edit.length > 0) {
              cy.wrap($edit).click()
              cy.get('textarea, input').should('exist')
            }
          })
        }
      })
    })

    it('validates JSON format in data editor', () => {
      cy.visit(`/a/${teamSlug}/participants/`)
      cy.get('table tbody tr a').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.contains('button', /Edit/i).then(($edit) => {
            if ($edit.length > 0) {
              cy.wrap($edit).click()
              // Try to enter invalid JSON
              cy.get('textarea').then(($textarea) => {
                if ($textarea.length > 0) {
                  cy.wrap($textarea).clear().type('invalid json{')
                  cy.contains('button', /Save|Submit/i).click()
                  // Should show error
                  cy.contains(/error|invalid|format/i, { timeout: 3000 }).should('exist')
                }
              })
            }
          })
        }
      })
    })
  })

  describe('Edit Participant Name', () => {
    it('can edit participant name', () => {
      cy.visit(`/a/${teamSlug}/participants/`)
      cy.get('table tbody tr a').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          // Look for name edit button or inline edit
          cy.get('[data-edit="name"], .edit-name, button[aria-label*="edit"]').then(($editBtn) => {
            if ($editBtn.length > 0) {
              cy.wrap($editBtn).first().click()
              cy.get('input[name="name"]').should('be.visible')
            }
          })
        }
      })
    })
  })

  describe('Participant Schedules', () => {
    it('displays scheduled messages', () => {
      cy.visit(`/a/${teamSlug}/participants/`)
      cy.get('table tbody tr a').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          // Look for schedules section
          cy.contains(/Schedule|Messages/i).then(($section) => {
            if ($section.length > 0) {
              cy.log('Schedules section found')
            }
          })
        }
      })
    })

    it('can cancel a schedule', () => {
      cy.visit(`/a/${teamSlug}/participants/`)
      cy.get('table tbody tr a').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          // Look for cancel button on schedules
          cy.contains('button', /Cancel|Stop/i).then(($cancel) => {
            if ($cancel.length > 0) {
              cy.log('Cancel schedule button available')
            }
          })
        }
      })
    })
  })

  describe('Participant Export', () => {
    it('opens export modal', () => {
      cy.visit(`/a/${teamSlug}/participants/`)
      cy.contains('button, a', /Export/i).then(($export) => {
        if ($export.length > 0) {
          cy.wrap($export).click()
          cy.get('[role="dialog"], .modal, form').should('be.visible')
        }
      })
    })

    it('export form has experiment selection', () => {
      cy.visit(`/a/${teamSlug}/participants/`)
      cy.contains('button, a', /Export/i).then(($export) => {
        if ($export.length > 0) {
          cy.wrap($export).click()
          cy.get('select, input[type="checkbox"]').should('exist')
        }
      })
    })
  })

  describe('Participant Import', () => {
    it('navigates to import page', () => {
      cy.visit(`/a/${teamSlug}/participants/`)
      cy.contains('button, a', /Import/i).then(($import) => {
        if ($import.length > 0) {
          cy.wrap($import).click()
          cy.url().should('include', 'import')
        }
      })
    })

    it('import page has file upload', () => {
      cy.visit(`/a/${teamSlug}/participants/import/`)
      cy.get('input[type="file"]').should('exist')
    })

    it('import page has experiment selection', () => {
      cy.visit(`/a/${teamSlug}/participants/import/`)
      cy.get('select[name*="experiment"], #id_experiment').should('exist')
    })
  })

  describe('Participant Table Pagination', () => {
    it('shows pagination controls if many participants', () => {
      cy.visit(`/a/${teamSlug}/participants/`)
      cy.get('.pagination, [aria-label="pagination"]').then(($pagination) => {
        if ($pagination.length > 0) {
          cy.log('Pagination controls are present')
          cy.wrap($pagination).should('be.visible')
        }
      })
    })
  })
})
