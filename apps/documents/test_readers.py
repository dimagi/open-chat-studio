from io import BytesIO

from apps.documents.readers import plaintext_reader


def test_plaintext_reader():
    # Create test content with special windows-1252 characters
    test_content = "Hello World! Special chars: café résumé"
    # Encode as windows-1252, to simulate a file with that encoding
    encoded_content = test_content.encode("windows-1252")
    assert encoded_content != test_content
    file_obj = BytesIO(encoded_content)

    # Call plaintext_reader
    result = plaintext_reader(file_obj)
    assert result.parts[0].content == "Hello World! Special chars: café résumé"
