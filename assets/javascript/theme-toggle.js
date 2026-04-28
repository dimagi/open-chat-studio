const darkTheme = 'dark';
const lightTheme = 'light';
const systemTheme = 'system';

function readStoredTheme() {
  try {
    return localStorage.getItem('theme');
  } catch {
    return null;
  }
}

function writeStoredTheme(theme) {
  try {
    localStorage.setItem('theme', theme);
  } catch {
    // ignore
  }
}

function syncDarkMode() {
  const theme = readStoredTheme() || systemTheme;
  setTheme(theme);
}

function setTheme(theme) {
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;

  if (theme === darkTheme || (theme === systemTheme && prefersDark)) {
    document.documentElement.classList.add('dark');
    document.documentElement.setAttribute('data-theme', darkTheme);
    // set a cookie and use it during server rendering to avoid a flicker across page loads
    document.cookie = `theme=${darkTheme};path=/;max-age=31536000`;
  } else {
    document.documentElement.classList.remove('dark');
    document.documentElement.setAttribute('data-theme', lightTheme);
    document.cookie = `theme=${lightTheme};path=/;max-age=31536000`;
  }
}

document.addEventListener('DOMContentLoaded', () => {

  document.getElementsByName("theme-dropdown").forEach((element) => {
    element.addEventListener('change', (event) => {
      const theme = event.target.value;
      writeStoredTheme(theme);
      setTheme(theme);
    })
  })

  // Watch for changes in the prefers-color-scheme
  const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
  mediaQuery.addEventListener("change", syncDarkMode);
});

syncDarkMode();
