'use strict'
import TomSelect from "tom-select";

let knownTags = [];
const instances = [];

function initOne(el) {
  if (el.classList.contains("tomselected")) {
    return;
  }
  const current = el.dataset.current || "";
  const options = knownTags.map((name) => ({value: name}));
  if (current && !knownTags.includes(current)) {
    options.push({value: current});
  }
  const ts = new TomSelect(el, {
    plugins: ["caret_position", "input_autogrow"],
    maxItems: 1,
    valueField: "value",
    labelField: "value",
    searchField: ["value"],
    options: options,
    items: current ? [current] : [],
    create: true,
    createFilter: (input) => {
      if (input.length > 100) {
        ts.dropdown_content.innerHTML = `<div class="ts-error-message" style="color: red; padding: 5px;">Tag name too long. Maximum 100 characters allowed.</div>`;
        return false;
      }
      return true;
    },
    onOptionAdd: (value) => {
      if (!knownTags.includes(value)) {
        knownTags.push(value);
        instances.forEach((other) => {
          if (other !== ts) {
            other.addOption({value: value});
          }
        });
      }
    },
  });
  instances.push(ts);
}

export const setupTagRuleSelects = (rootEl) => {
  const root = rootEl || document;
  root.querySelectorAll(".tag-rule-select:not(.tomselected)").forEach(initOne);
};

export const bootstrapTagRuleSelects = () => {
  const data = document.getElementById("tag-rule-available-tags");
  if (data) {
    try {
      knownTags = JSON.parse(data.textContent) || [];
    } catch (e) {
      knownTags = [];
    }
  }
  setupTagRuleSelects();
};
