'use strict'

const DOWNLOAD_COOKIE_PREFIX = 'session_export_download_'
const DOWNLOAD_TIMEOUT_MS = 15000

/** Return the download response token currently exposed through cookies. */
function readDownloadCookie(token) {
  const name = `${DOWNLOAD_COOKIE_PREFIX}${token}`
  const cookie = document.cookie
    .split('; ')
    .find((entry) => entry.startsWith(`${name}=`))
  return cookie ? decodeURIComponent(cookie.slice(name.length + 1)) : null
}

/** Remove the per-request download response token after observing it. */
function clearDownloadCookie(token) {
  document.cookie = `${DOWNLOAD_COOKIE_PREFIX}${token}=; Max-Age=0; path=/`
}

/** Generate a unique token to associate this browser request with its response cookie. */
function newDownloadToken() {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`
}

/** Return the Alpine state and lifecycle handlers for a session CSV download. */
export function sessionExport() {
  return {
    exportState: 'idle',
    downloadToken: null,
    pollTimer: null,
    timeoutTimer: null,
    idleTimer: null,

    /** Start one export request and wait for its response cookie. */
    start(event) {
      if (this.exportState === 'loading') {
        event.preventDefault()
        return
      }

      this.clearTimers()
      this.downloadToken = newDownloadToken()
      const link = event.currentTarget
      const url = new URL(link.href, window.location.href)
      url.searchParams.set('download_token', this.downloadToken)
      link.href = url.toString()
      this.exportState = 'loading'
      this.pollTimer = setInterval(() => {
        if (readDownloadCookie(this.downloadToken) === '1') {
          this.finish('success')
        }
      }, 100)
      this.timeoutTimer = setTimeout(() => this.finish('error'), DOWNLOAD_TIMEOUT_MS)
    },

    /** Show the response result and release the request guard. */
    finish(state) {
      this.clearTimers()
      if (state === 'success') {
        clearDownloadCookie(this.downloadToken)
        this.exportState = 'success'
        this.idleTimer = setTimeout(() => {
          this.exportState = 'idle'
        }, 3000)
      } else {
        this.exportState = 'error'
      }
    },

    /** Stop timers from the previous request or component state. */
    clearTimers() {
      if (this.pollTimer) clearInterval(this.pollTimer)
      if (this.timeoutTimer) clearTimeout(this.timeoutTimer)
      if (this.idleTimer) clearTimeout(this.idleTimer)
      this.pollTimer = null
      this.timeoutTimer = null
      this.idleTimer = null
    },
  }
}

window.sessionExport = sessionExport
