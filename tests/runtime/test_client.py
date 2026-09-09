import json
from unittest.mock import patch

from swesmith.llm import complete


class _Response:
    def __init__(self, payload: dict):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.payload


def test_complete_posts_to_ptie_without_authentication():
    response = _Response(
        {
            "id": "test",
            "model": "glm-5.3-flash",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ready"},
                }
            ],
        }
    )
    with patch("urllib.request.urlopen", return_value=response) as urlopen:
        result = complete(
            messages=[{"role": "user", "content": "ping"}],
            api_base="http://127.0.0.1:8000/v1",
        )

    request = urlopen.call_args.args[0]
    body = json.loads(request.data)
    assert request.full_url == "http://127.0.0.1:8000/v1/chat/completions"
    assert "Authorization" not in request.headers
    assert body["model"] == "glm-5.3-flash"
    assert result.choices[0].message.content == "ready"
