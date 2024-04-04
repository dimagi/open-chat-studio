var urlData = document.getElementById('tag-multiselect');
const linkTagUrl = urlData.getAttribute("data-linkTagUrl");
const unlinkTagUrl = urlData.getAttribute("data-unlinkTagUrl");
const userCanCreate = JSON.parse(urlData.getAttribute("data-userCanCreate"));

let controlInstances = [];

var addTag = function(name, objectInfo) {
  return function() {
    let postData = {swap: 'none', values: {"tag_name": arguments[0], "object_info": objectInfo}};
    htmx.ajax('POST', linkTagUrl, postData);
    let dropdown_option = {text: arguments[0], value: arguments[0]};
    // Add the new tag to all existing TomSelect instances. This will do nothing if it already exists
    controlInstances.forEach((controlInstance) => {
      controlInstance.addOption(dropdown_option);
    });
  };
};

var removeTag = function(name, objectInfo) {
  return function() {
    let postData = {swap: 'none', values: {"tag_name": arguments[0], "object_info": objectInfo}};
    htmx.ajax('POST', unlinkTagUrl, postData);
  };
};

document.querySelectorAll('.tag-multiselect').forEach((el)=> {
  let objectInfo = el.getAttribute("data-info");
  let control = new TomSelect(el, {
    maxItems: null,
    create: userCanCreate,
    onItemAdd: addTag('onItemAdd', objectInfo),
    onItemRemove: removeTag('onItemRemove', objectInfo)
  });
  controlInstances.push(control);
});
