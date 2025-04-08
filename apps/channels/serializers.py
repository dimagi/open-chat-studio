from rest_framework import serializers


class MessageSerializer(serializers.Serializer):
    timestamp = serializers.CharField()
    message_id = serializers.UUIDField()
    ciphertext = serializers.CharField()
    tag = serializers.CharField()
    nonce = serializers.CharField()


class CommCareConnectMessageSerializer(serializers.Serializer):
    channel_id = serializers.UUIDField()
    messages = MessageSerializer(many=True)


class ApiMessageSerializer(serializers.Serializer):
    message = serializers.CharField(label="User message")
    session = serializers.CharField(required=False, label="Optional session ID")


class ApiResponseAttachmentSerializer(serializers.Serializer):
    file_name = serializers.CharField()
    link = serializers.CharField()


class ApiResponseMessageSerializer(serializers.Serializer):
    response = serializers.CharField(label="AI response")
    attachments = serializers.ListField(label="List of file URLs", child=ApiResponseAttachmentSerializer())
