## Usage
### Adding tags to your model
To add tagging ability to your model, simply add the `TaggedModelMixin` to your model. This will add a `tags`
field and provide helper methods to manage these tags.

### Include the `tag_multiselect.html` template in your template:

```
{% include "generic/tag_multiselect.html" with object_tags=object_variable %}
```

Be sure to pass the available tags as a template variable called `available_tags`.

### Load the script

```html
<script
    id="tag-multiselect"  (you must use this ID)
    src="{% static './tag_multiselect.js' %}"
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

If you want to show the number of comments that your object has and have it update as you add or remove comments, you can use the following `span` implementation (based on AlpineJS) that listens to the `object.event_name` event that will be dispatched each time the `user_comments.html` page is initialized. These events are unique per object, so it wouldn't interfere with other events.

```html
<span x-data="{commentCount: 0}" @{{ your_object.event_name }}.window="commentCount = $event.detail" x-text="commentCount"></span>
```

#### Example
```html
<h1>
    Comment count: <span x-data="{commentCount: 0}" @{{ your_object.event_name }}.window="commentCount = $event.detail" x-text="commentCount"></span>
</h1>
```

