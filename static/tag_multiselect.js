var urlData = document.getElementById('tag-multiselect');
const linkTagUrl = urlData.getAttribute("data-linkTagUrl");
const unlinkTagUrl = urlData.getAttribute("data-unlinkTagUrl");

var addTag = function(name, objectInfo) {
  return function() {
    let postData = {swap: 'none', values: {"tag_name": arguments[0], "object_info": objectInfo}};
    htmx.ajax('POST', linkTagUrl, postData);
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

  new TomSelect(el, {
    maxItems: null,
    onItemAdd: addTag('onItemAdd', objectInfo),
    onItemRemove: removeTag('onItemRemove', objectInfo)
  });
});
