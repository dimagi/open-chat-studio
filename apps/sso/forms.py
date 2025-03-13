from allauth.socialaccount.forms import SignupForm


class SsoSignupForm(SignupForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "email" in self.initial:
            self.fields["email"].widget.attrs = {"readonly": "readonly"}
