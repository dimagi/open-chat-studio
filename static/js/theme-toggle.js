document.addEventListener("DOMContentLoaded", function () {

// Function to set the theme
function setTheme(theme) {
  document.body.setAttribute("data-theme", theme); // Apply to body
  localStorage.setItem("theme", theme);
  const themeToggle = document.getElementById("theme-toggle-base");
  if (themeToggle) {
    themeToggle.checked = theme === "light"; // Sync checkbox state
  }
 }

// Function to toggle the theme
function toggleTheme() {
  const currentTheme = localStorage.getItem("theme") || "light";
  const newTheme = currentTheme === "light" ? "dark" : "light";
  setTheme(newTheme);
}

// Function to initialize the theme
function initializeTheme() {
  const savedTheme = localStorage.getItem("theme");
  const systemTheme = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  const theme = savedTheme || systemTheme;
  setTheme(theme); // Set the theme and sync the checkbox
}

// Event delegation for theme toggle
document.body.addEventListener('change', function (event) {
  if (event.target && event.target.id === 'theme-toggle') {
    toggleTheme();
  }
});

// Initialize the theme when the page loads
initializeTheme();
});