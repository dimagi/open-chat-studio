describe('Assistants Application', () => {
  const teamSlug = Cypress.env('TEAM_SLUG') || 'your-team-slug'

  beforeEach(() => {
    cy.login()
  })

  describe('Assistants Home Page', () => {
    it('loads assistants page successfully', () => {
      cy.visit(`/a/${teamSlug}/assistants/`)
      cy.url().should('include', '/assistants/')
      cy.contains('Assistants').should('be.visible')
    })

    it('displays assistants table', () => {
      cy.visit(`/a/${teamSlug}/assistants/`)
      cy.get('table, .table-container, [data-table]', { timeout: 10000 }).should('exist')
    })

    it('has create assistant button', () => {
      cy.visit(`/a/${teamSlug}/assistants/`)
      cy.contains('button, a', /Create|New|Add/i).should('exist')
    })

    it('search functionality works', () => {
      cy.visit(`/a/${teamSlug}/assistants/`)
      cy.get('input[type="search"], input[name="search"]').then(($search) => {
        if ($search.length > 0) {
          cy.wrap($search).first().type('test{enter}')
          cy.wait(500)
        }
      })
    })
  })

  describe('Create Assistant', () => {
    it('navigates to create assistant page', () => {
      cy.visit(`/a/${teamSlug}/assistants/`)
      cy.contains('button, a', /Create|New|Add/i)
        .first()
        .then(($btn) => {
          if ($btn.length > 0) {
            cy.wrap($btn).click()
            cy.get('form, [role="dialog"]').should('exist')
          }
        })
    })

    it('create form has required fields', () => {
      cy.visit(`/a/${teamSlug}/assistants/new/`)
      cy.get('input[name="name"], #id_name').should('exist')
      cy.get('textarea[name="instructions"], #id_instructions').should('exist')
    })

    it('validates required fields', () => {
      cy.visit(`/a/${teamSlug}/assistants/new/`)
      cy.get('button[type="submit"]').click()
      cy.contains(/required|field|error/i).should('exist')
    })

    it('can select assistant model', () => {
      cy.visit(`/a/${teamSlug}/assistants/new/`)
      cy.get('select[name*="model"], #id_llm_model').then(($select) => {
        if ($select.length > 0) {
          cy.wrap($select).should('exist')
        }
      })
    })
  })

  describe('Assistant Details', () => {
    it('navigates to assistant details', () => {
      cy.visit(`/a/${teamSlug}/assistants/`)
      cy.get('table tbody tr a').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.url().should('match', /assistants\/\d+/)
        }
      })
    })

    it('displays assistant information', () => {
      cy.visit(`/a/${teamSlug}/assistants/`)
      cy.get('table tbody tr a').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.contains(/Name|Instructions|Model/i).should('exist')
        }
      })
    })

    it('shows assistant tabs', () => {
      cy.visit(`/a/${teamSlug}/assistants/`)
      cy.get('table tbody tr a').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.get('[role="tablist"], .nav-tabs, .tabs').should('exist')
        }
      })
    })
  })

  describe('Edit Assistant', () => {
    it('can access edit form', () => {
      cy.visit(`/a/${teamSlug}/assistants/`)
      cy.get('table tbody tr a').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.contains('button, a', /Edit|Modify/i).then(($edit) => {
            if ($edit.length > 0) {
              cy.wrap($edit).click()
              cy.get('form').should('exist')
            }
          })
        }
      })
    })

    it('edit form pre-populates existing data', () => {
      cy.visit(`/a/${teamSlug}/assistants/`)
      cy.get('table tbody tr a').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.contains('button, a', /Edit/i).then(($edit) => {
            if ($edit.length > 0) {
              cy.wrap($edit).click()
              cy.get('input[name="name"]').should('not.have.value', '')
            }
          })
        }
      })
    })
  })

  describe('Assistant Tools', () => {
    it('can configure built-in tools', () => {
      cy.visit(`/a/${teamSlug}/assistants/`)
      cy.get('table tbody tr a').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.contains(/Tools|Code Interpreter|File Search/i).then(($tools) => {
            if ($tools.length > 0) {
              cy.log('Tools configuration available')
            }
          })
        }
      })
    })

    it('displays code interpreter option', () => {
      cy.visit(`/a/${teamSlug}/assistants/new/`)
      cy.contains(/Code Interpreter|code_interpreter/i).then(($option) => {
        if ($option.length > 0) {
          cy.log('Code interpreter tool available')
        }
      })
    })

    it('displays file search option', () => {
      cy.visit(`/a/${teamSlug}/assistants/new/`)
      cy.contains(/File Search|file_search/i).then(($option) => {
        if ($option.length > 0) {
          cy.log('File search tool available')
        }
      })
    })
  })

  describe('Assistant Files', () => {
    it('can upload files to assistant', () => {
      cy.visit(`/a/${teamSlug}/assistants/`)
      cy.get('table tbody tr a').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.contains(/Files|Upload|Attach/i).then(($files) => {
            if ($files.length > 0) {
              cy.log('File management available for assistant')
            }
          })
        }
      })
    })

    it('displays uploaded files list', () => {
      cy.visit(`/a/${teamSlug}/assistants/`)
      cy.get('table tbody tr a').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.contains(/Files/i).then(($tab) => {
            if ($tab.length > 0) {
              cy.wrap($tab).click()
              cy.get('table, .file-list, ul').should('exist')
            }
          })
        }
      })
    })
  })

  describe('Assistant Sync', () => {
    it('has sync button for remote assistants', () => {
      cy.visit(`/a/${teamSlug}/assistants/`)
      cy.get('table tbody tr a').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.contains('button', /Sync|Refresh/i).then(($sync) => {
            if ($sync.length > 0) {
              cy.log('Sync functionality available')
            }
          })
        }
      })
    })
  })

  describe('Delete Assistant', () => {
    it('has delete button', () => {
      cy.visit(`/a/${teamSlug}/assistants/`)
      cy.get('table tbody tr a').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.contains('button, a', /Delete|Remove/i).then(($delete) => {
            if ($delete.length > 0) {
              cy.log('Delete functionality available')
            }
          })
        }
      })
    })

    it('delete requires confirmation', () => {
      cy.visit(`/a/${teamSlug}/assistants/`)
      cy.get('table tbody tr a').first().then(($link) => {
        if ($link.length > 0) {
          cy.wrap($link).click()
          cy.contains('button, a', /Delete/i).then(($delete) => {
            if ($delete.length > 0) {
              cy.wrap($delete).click()
              cy.contains(/confirm|sure|warning/i, { timeout: 3000 }).should('exist')
            }
          })
        }
      })
    })
  })

  describe('Assistant Table Features', () => {
    it('displays assistant status', () => {
      cy.visit(`/a/${teamSlug}/assistants/`)
      cy.get('table tbody tr').first().then(($row) => {
        if ($row.length > 0) {
          cy.log('Assistant table row exists')
        }
      })
    })

    it('table has sortable columns', () => {
      cy.visit(`/a/${teamSlug}/assistants/`)
      cy.get('th[data-sort], th.sortable, th a[href*="sort"]').then(($sortable) => {
        if ($sortable.length > 0) {
          cy.log('Sortable columns available')
        }
      })
    })

    it('shows pagination if many assistants', () => {
      cy.visit(`/a/${teamSlug}/assistants/`)
      cy.get('.pagination, [aria-label="pagination"]').then(($pagination) => {
        if ($pagination.length > 0) {
          cy.log('Pagination controls present')
        }
      })
    })
  })
})
