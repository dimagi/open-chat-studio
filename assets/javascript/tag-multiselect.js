'use strict'
import htmx from 'htmx.org'
import TomSelect from "tom-select";

const urlData = document.getElementById('tag-multiselect');
const linkTagUrl = urlData.getAttribute("data-linkTagUrl");
const unlinkTagUrl = urlData.getAttribute("data-unlinkTagUrl");
const tsBlur = new Event("ts-blur");
let controlInstances = [];

function addTag (name, el, objectInfo) {
  return function () {
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
    let postData = {source: el, swap: 'none', values: {"tag_name": arguments[0], "object_info": objectInfo}};
    htmx.ajax('POST', unlinkTagUrl, postData);
  };
}

/**
 * Only focus the editor htmx just swapped in. Focusing any other newly-found one
 * would steal focus from an editor the user is already typing in.
 */
function configureTomSelect(swapTarget) {
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
    if (!swapTarget || swapTarget.contains(el)) {
      control.focus();
    }
  });
}

/**
 * htmx swaps out a tag editor's DOM directly, so TomSelect's own destroy() never
 * runs and its document-level mousedown listener leaks. Checking wrapper.isConnected
 * catches this regardless of swap style or whether a swap even completed, which a
 * beforehand guess at the swap target can't.
 */
function pruneDetachedControls() {
  controlInstances = controlInstances.filter((control) => {
    if (!control.wrapper.isConnected) {
      control.destroy();
      return false;
    }
    return true;
  });
}

export const setupTagSelects = () => {
  configureTomSelect();
  htmx.on("htmx:afterSwap", (evt) => {
    pruneDetachedControls();
    /**
     * evt.detail.target is the pre-swap target and goes stale on an outerHTML swap
     * (the element it points to gets replaced); evt.target is the live post-swap
     * element htmx actually dispatches the event on.
     */
    configureTomSelect(evt.target);
  });
}
