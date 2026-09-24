import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

describe("widget theme synchronization", () => {
  beforeEach(() => {
    vi.resetModules();
    localStorage.clear();
    document.body.innerHTML = "";
    document.documentElement.removeAttribute("data-theme");
    window.matchMedia = vi.fn(() => ({
      matches: false,
      addEventListener: vi.fn(),
    }));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("applies the resolved theme to widgets mounted after initial synchronization", async () => {
    localStorage.setItem("theme", "dark");
    await import("./theme-toggle.js");
    const widget = document.createElement("open-chat-studio-widget");
    document.body.appendChild(widget);
    await Promise.resolve();
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(widget.getAttribute("theme")).toBe("dark");
  });

  it("updates an existing widget through the theme dropdown in both directions", async () => {
    const dropdown = document.createElement("select");
    dropdown.name = "theme-dropdown";
    dropdown.innerHTML =
      '<option value="light">Light</option><option value="dark">Dark</option>';
    document.body.appendChild(dropdown);
    const widget = document.createElement("open-chat-studio-widget");
    document.body.appendChild(widget);
    await import("./theme-toggle.js");
    document.dispatchEvent(new Event("DOMContentLoaded"));

    dropdown.value = "dark";
    dropdown.dispatchEvent(new Event("change", { bubbles: true }));
    expect(widget.getAttribute("theme")).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");

    dropdown.value = "light";
    dropdown.dispatchEvent(new Event("change", { bubbles: true }));
    expect(widget.getAttribute("theme")).toBe("light");
    expect(document.documentElement.dataset.theme).toBe("light");
  });
});
