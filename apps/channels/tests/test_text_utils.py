import re

from apps.channels.text_utils import URL_REGEX, strip_urls_and_emojis


def test_strip_urls_and_emojis():
    """
    Test that unique urls are extracted and emojis are stripped out
    """
    text = (
        "Hey there! 😊 Check out this amazing website: https://www.example.com! Also, don't forget to visit"
        " http://www.another-site.org. If you're a fan of coding, you'll love"
        " https://developer.mozilla.org/some/path. Have you seen this awesome cat video? 🐱🐾 Watch it at"
        " [https://www.catvideos.com](https://www.catvideos.com). Let's stay connected on social media: Twitter"
        " (https://twitter.com) and Facebook (https://facebook.com?page=page1). Can't wait to see you there! 🎉✨"
    )
    expected_text = (
        "Hey there!  Check out this amazing website: ! Also, don't forget to visit . If you're a fan of coding, "
        "you'll love . Have you seen this awesome cat video?  Watch it at [](). Let's stay connected on social "
        "media: Twitter () and Facebook (). Can't wait to see you there! "
    )

    output, urls = strip_urls_and_emojis(text)
    assert output == expected_text
    assert set(urls) == {
        "https://www.example.com",
        "http://www.another-site.org",
        "https://developer.mozilla.org/some/path",
        "https://twitter.com",
        "https://www.catvideos.com",
        "https://facebook.com?page=page1",
    }


def test_url_regex():
    url_pattern = re.compile(URL_REGEX)
    expected_matches = [
        "http://www.example.com",
        "http://www.example.co.za",
        "http://www.example.com/",
        "http://www.example.com?key1=val1&key2=val2",
        "http://www.example.com/some/path?key1=val1&key2=val2",
        "http://example.com",
        "http://example.co.za",
        "http://example.com/",
        "http://example.com?key1=val1&key2=val2",
        "http://example.com/some/path?key1=val1&key2=val2",
        "https://www.example.com",
        "https://www.example.co.za",
        "https://www.example.com/",
        "https://www.example.com?key1=val1&key2=val2",
        "https://www.example.com/some/path?key1=val1&key2=val2",
        "https://example.com",
        "https://example.co.za",
        "https://example.com/",
        "https://example.com?key1=val1&key2=val2",
        "https://example.com/some/path?key1=val1&key2=val2",
    ]

    no_matches = [
        "https//example.com",
        "htrps//example.com",
        "htrps\\example.com",
        "http://example.",
        "http://example!",
        "http://example?",
    ]
    matches = url_pattern.findall("\n".join(expected_matches))

    assert len(matches) == 20

    for url in expected_matches:
        assert url in matches

    for url in no_matches:
        assert url not in matches
