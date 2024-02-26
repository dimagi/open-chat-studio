def log(session):
    print(session.chat.messages.last().content)
    return session.chat.messages.last().content
