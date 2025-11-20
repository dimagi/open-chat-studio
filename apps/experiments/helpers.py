def get_real_user_or_none(user):
    if user.is_anonymous:
        return None
    else:
        return user
