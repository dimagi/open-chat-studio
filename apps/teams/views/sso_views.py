from allauth.socialaccount.adapter import get_adapter
from allauth.socialaccount.models import SocialApp
from django.shortcuts import redirect, render
from django.views import View


class CustomLoginView(View):
    template_name = "teams/sso_login.html"

    def get(self, request):
        return render(request, self.template_name)

    def post(self, request):
        email = request.POST.get("email")
        domain = email.split("@")[-1].lower()  # Extract domain
        print(domain)

        # Map domains to providers (e.g., Azure AD for 'clientdomain.com')
        app = SocialApp.objects.filter(settings__email_domains__contains=[domain]).first()
        if app:
            provider = app.get_provider(request)
            # domain_to_provider = {
            #     'clientdomain.com': 'microsoft',
            #     'otherdomain.com': 'google',
            # }
            #
            # provider_id = domain_to_provider.get(domain)
            #
            # if provider_id:
            # Store email in session to validate later
            request.session["initial_login_email"] = email
            request.session.modified = True

            # Redirect to the provider's login URL
            # provider = self.get_provider(provider_id)
            return redirect(provider.get_login_url(request))
        else:
            # Handle invalid domains (e.g., show error or fallback)
            return render(request, self.template_name, {"error": "Invalid domain"})

    def get_provider(self, provider_id):
        adapter = get_adapter(self.request)
        app = adapter.get_app(self.request, provider=provider_id)
        return app.get_provider(self.request)
