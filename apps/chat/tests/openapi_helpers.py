def make_openapi_schema(properties, name="Test API", path="/test", method="get", openapi_version="3.0.0"):
    return {
        "openapi": openapi_version,
        "info": {"title": name, "version": "1.0.0"},
        "servers": [{"url": "https://example.com"}],
        "paths": {
            path: {
                method: {
                    "summary": f"{method.upper()} {path} endpoint",
                    **properties,
                }
            }
        },
    }
