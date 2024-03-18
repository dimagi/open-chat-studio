## Usage
### Include the `tag_multiselect.html` template in your template:

```
{% include "generic/tag_multiselect.html" with object_tags=experiment_session.chat.get_linked_tags object_info=experiment_session.chat.object_info %}
```
### Load the script

```html
<div id="linkTagUrl" data-url="{% url 'annotations:link_tag' request.team.slug %}"></div>
<div id="unlinkTagUrl" data-url="{% url 'annotations:unlink_tag' request.team.slug %}"></div>
<script src="{% static './tag_multiselect.js' %}"></script
```

Note: If you're going to be tagging mutiple objects on the same page (say, a chat and chat messages), you'll have to load the script in the template that is the parent of both object templates. For example, `experiment_session_view.html` includes both `experiment_details.html` (the chat) and `experiment_chat.html` (chat messages), so that makes `experiment_session_view.html` the "parent" template of both of these object templates.
