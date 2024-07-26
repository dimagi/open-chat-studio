from rest_framework import serializers


class StatsSerializer(serializers.Serializer):
    date = serializers.DateField()
    count = serializers.IntegerField()
