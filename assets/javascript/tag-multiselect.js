'use strict'
import htmx from 'htmx.org'
import TomSelect from "tom-select";

const tsBlur = new Event("ts-blur");
let controlInstances = [];

// Lazy-load URLs from DOM when needed
function getUrls() {
  const urlData = document.getElementById('tag-multiselect');
  if (!urlData) {
    return { linkTagUrl: null, unlinkTagUrl: null };
  }
  return {
    linkTagUrl: urlData.getAttribute("data-linkTagUrl"),
    unlinkTagUrl: urlData.getAttribute("data-unlinkTagUrl")
  };
}

function addTag (name, el, objectInfo) {
  return function () {
    const { linkTagUrl } = getUrls();
    if (!linkTagUrl) return;

    let postData = {source: el, swap: 'none', values: {"tag_name": arguments[0], "object_info": objectInfo}};
    htmx.ajax('POST', linkTagUrl, postData);
    let dropdown_option = {text: arguments[0], value: arguments[0]};
    // Add the new tag to all existing TomSelect instances. This will do nothing if it already exists
    controlInstances.forEach((controlInstance) => {
      controlInstance.addOption(dropdown_option);
    });
  };
}

function removeTag (name, el, objectInfo) {
  return function () {
    const { unlinkTagUrl } = getUrls();
    if (!unlinkTagUrl) return;

    let postData = {source: el, swap: 'none', values: {"tag_name": arguments[0], "object_info": objectInfo}};
    htmx.ajax('POST', unlinkTagUrl, postData);
  };
}

function configureTomSelect() {
  const filter = '.tag-multiselect:not(.tomselected):not(.ts-wrapper)';
  document.querySelectorAll(filter).forEach((el) => {
    let objectInfo = el.getAttribute("data-info");
    let allowCreate = el.getAttribute("data-allowCreate") !== "false";

    let control = new TomSelect(el, {
      plugins: ["remove_button", "caret_position", "input_autogrow"],
      maxItems: null,
      create: allowCreate,
      createFilter: allowCreate ? (input) => {
        if (input.length > 100) {
          el.tomselect.dropdown_content.innerHTML = `<div class="ts-error-message" style="color: red; padding: 5px;">Tag name too long. Maximum 100 characters allowed.</div>`;
          return false;
        }
        return true;
      } : false,
      onItemAdd: addTag('onItemAdd', el, objectInfo),
      onItemRemove: removeTag('onItemRemove', el, objectInfo),
      onBlur: () => {
        el.dispatchEvent(tsBlur);
      }
    });
    controlInstances.push(control);
    control.focus();
  });
}

export const setupTagSelects = () => {
  configureTomSelect();
  htmx.on("htmx:afterSwap", () => { configureTomSelect() });
}
