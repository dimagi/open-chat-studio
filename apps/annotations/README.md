## Usage
### Adding tags to your model
To add tagging ability to your model, simply add the `TaggedModelMixin` to your model. This will add a `tags`
field and provide helper methods to manage these tags.

### Include the `tag_ui.html` template in your template:

```
{% include "annotations/tag_ui.html" with object_tags=object_variable %}
```

Be sure to pass the available tags as a template variable called `available_tags`.

### Load the script

```html
<script
    type="module"
    id="tag-multiselect"  (you must use this ID)
    src="{% static "js/tagMultiselect-bundle.js" %}"
    data-linkTagUrl="{% url 'annotations:link_tag' request.team.slug %}"
    data-unlinkTagUrl="{% url 'annotations:unlink_tag' request.team.slug %}"
    >
</script>
```

If you're going to be tagging mutiple objects on the same page (say, a chat and chat messages), you'll have to load the script in the template that is the parent of both object templates. For example, `experiment_session_view.html` includes both `experiment_details.html` (the chat) and `experiment_chat.html` (chat messages), so that makes `experiment_session_view.html` the "parent" template of both of these object templates.

### Adding comments to your model
Simply add the `UserCommentsMixin` to your model. This will add a `comments` field and provide some helper methods.
in your template, add
```
{% include "experiments/components/user_comments.html" with object=<your-object> %}
```

If you want to show the number of comments that your object has and have it update as you add or remove comments, you can following this example's code:

```html
<h1>
    Comment count: <span id="{{ object.comment_count_element_id }}">{{ object.get_user_comments|length }}</span>
</h1>
```

The initial count comes from `object.get_user_comments|length`. Whenever a comment is added or removed, a new template is being rendered for that comment section. Part of that template is this:

```html
{% if update_count|default:False %}
    <span id="{{ object.comment_count_element_id }}" hx-swap-oob="true">{{ object.get_user_comments|length }}</span>
{% endif %}
```
`update_count` will be true, which means that the element with `id="{{ object.comment_count_element_id }}"` will be replaced by the new count.
