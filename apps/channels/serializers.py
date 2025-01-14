from rest_framework import serializers


class MessageSerializer(serializers.Serializer):
    timestamp = serializers.CharField()
    message_id = serializers.UUIDField()
    ciphertext = serializers.CharField()
    tag = serializers.CharField()
    nonce = serializers.CharField()


class CommCareConnectMessageSerializer(serializers.Serializer):
    channel_id = serializers.UUIDField()
    messages = serializers.ListField(child=MessageSerializer())
