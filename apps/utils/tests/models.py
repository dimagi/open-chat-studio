from django.db import models


class Bot(models.Model):
    name = models.CharField(max_length=100)
    tools = models.ManyToManyField("Tool")

    def __str__(self):
        return self.name


class Tool(models.Model):
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name


class Param(models.Model):
    name = models.CharField(max_length=100)
    tool = models.ForeignKey(Tool, on_delete=models.CASCADE)

    def __str__(self):
        return self.name
