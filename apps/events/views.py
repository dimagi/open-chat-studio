from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render

from apps.events.forms import EventActionForm, StaticTriggerForm
from apps.events.models import StaticTrigger


def create_static_event_view(request, team_slug: str, experiment_id: str):
    if request.method == "POST":
        action_form = EventActionForm(request.POST)
        if action_form.is_valid():
            saved_action = action_form.save(experiment_id=experiment_id)
            trigger_form = StaticTriggerForm(request.POST)
            if trigger_form.is_valid():
                static_trigger = trigger_form.save(commit=False, experiment_id=experiment_id)
                static_trigger.action = saved_action
                static_trigger.save()
                # TODO: return HttpResponseRedirect(reverse("experiment"))
                return JsonResponse({"success": True, "message": "Static trigger created successfully."})
        return JsonResponse({"success": False, "message": "There was an error processing your request."})
    else:
        action_form = EventActionForm()
        static_trigger_form = StaticTriggerForm()
    context = {
        "action_form": action_form,
        "static_trigger_form": static_trigger_form,
    }
    return render(request, "events/create_static_event.html", context)


def edit_static_event_view(request, team_slug: str, experiment_id: str, static_trigger_id):
    # Fetch the StaticTrigger instance you wish to edit
    static_trigger = get_object_or_404(StaticTrigger, id=static_trigger_id, experiment_id=experiment_id)
    if request.method == "POST":
        action_form = EventActionForm(request.POST, instance=static_trigger.action)
        trigger_form = StaticTriggerForm(request.POST, instance=static_trigger)

        if action_form.is_valid() and trigger_form.is_valid():
            action_form.save(experiment_id=experiment_id)
            static_trigger = trigger_form.save(experiment_id=experiment_id)

            # return HttpResponseRedirect(reverse("experiment"))  # Redirect to a specific URL
            return JsonResponse({"success": True, "message": "Static trigger updated successfully."})
        else:
            # Return an error response if forms are not valid
            return JsonResponse({"success": False, "message": "There was an error processing your request."})
    else:
        # Instantiate the forms with instance data for GET requests
        action_form = EventActionForm(instance=static_trigger.action)
        trigger_form = StaticTriggerForm(instance=static_trigger)

    context = {
        "action_form": action_form,
        "static_trigger_form": trigger_form,
    }
    return render(request, "events/create_static_event.html", context)


# def create_event_view(request, team_slug: str):
#     action_form = EventActionForm()
#     static_trigger_form = StaticTriggerForm()
#     timeout_trigger_form = TimeoutTriggerForm()

#     context = {
#         "action_form": action_form,
#         "static_trigger_form": static_trigger_form,
#         "timeout_trigger_form": timeout_trigger_form,
#     }
#     return render(request, "events/create_form.html", context)
