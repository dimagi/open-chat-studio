import json


def create_synthetic_voices_from_file(SyntheticVoiceModel, file_path: str):
    """Use this utility to load new synthetic voices from a json file. The format of the file should be:
    {
        "service": "your-service", // i.e. "AWS". This should be one of SyntheticVoice.SERVICES
        "voices": [
            {
                "language": "Arabic",
                "language_code": "arb",
                "name": "Zeina",
                "gender": "Female",
                "neural": false
            },
            {
                ...
            },
        ]
    }
    """
    voice_data = {}

    with open(file_path) as json_file:
        voice_data = json.load(json_file)

    model_fields = [f.name for f in SyntheticVoiceModel._meta.get_fields()]

    voices_created = 0
    service = voice_data["service"]
    for voice_data in voice_data["voices"]:
        voice_data = {**voice_data, "service": service}
        voice_fields = {key: value for key, value in voice_data.items() if key in model_fields}
        _, created = SyntheticVoiceModel.objects.update_or_create(**voice_fields)

        if created:
            voices_created += 1
    print(f"{voices_created} synthetic voices were created for {service}")
