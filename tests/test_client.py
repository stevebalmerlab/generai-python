import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from generai import (
    APIError,
    GenerAI,
    JobFailed,
    PollingTimeout,
    ProtocolError,
    TransportError,
    credits,
    new_idempotency_key,
)


def client(handler, **kwargs):
    return GenerAI("test-secret", transport=httpx.MockTransport(handler), retry_delay=0, **kwargs)


def test_all_read_routes_and_quote():
    seen = []

    def handler(request):
        assert request.headers["Authorization"] == "Bearer test-secret"
        seen.append((request.method, request.url.path, dict(request.url.params)))
        if request.method == "POST":
            payload = json.loads(request.content)
            assert payload["is_long"] == "1"
            assert json.loads(payload["dialogue_edits"]) == {"0": "Hello"}
            assert "unused" not in payload
        return httpx.Response(200, json={"items": [], "page_size": 50})

    with client(handler) as api:
        api.me()
        api.openapi()
        assert api.categories(model="wan22", category_type="safe") == []
        api.category("safe", 1)
        api.generations(2)
        api.generation(99)
        api.quote("safe", 1, is_long=True, dialogue_edits={0: "Hello"}, unused=None)
    assert [item[1] for item in seen] == [
        "/api/v1/me", "/api/v1/openapi", "/api/v1/categories", "/api/v1/categories/safe/1",
        "/api/v1/generations", "/api/v1/generations/99", "/api/v1/generations/quote",
    ]
    assert seen[2][2] == {"model": "wan22", "category_type": "safe"}
    assert credits("0.1") + credits("0.2") == credits("0.3")


def test_text_only_create_is_multipart_and_key_is_returned():
    def handler(request):
        assert "multipart/form-data" in request.headers["content-type"]
        assert b'name="user_prompt"' in request.content
        assert "Lighthouse at sunset 🌅".encode() in request.content
        assert request.headers["Idempotency-Key"] == "my-persisted-key"
        return httpx.Response(202, json={"job_id": 5, "cost": "16.00"})

    with client(handler) as api:
        result = api.create_image(19, "Lighthouse at sunset 🌅", idempotency_key="my-persisted-key")
        assert result["idempotency_key"] == "my-persisted-key"


def test_retries_reopen_upload_and_preserve_key(tmp_path):
    path = tmp_path / "image.png"
    payload = b"unique-file-bytes" * 500
    path.write_bytes(payload)
    keys = []

    def handler(request):
        keys.append(request.headers["Idempotency-Key"])
        assert payload in request.content
        if len(keys) == 1:
            raise httpx.ReadTimeout("lost response")
        if len(keys) == 2:
            return httpx.Response(503)
        return httpx.Response(200, json={"job_id": 5, "replayed": True})

    with client(handler) as api:
        result = api.edit_image(1, path)
    assert keys == [result["idempotency_key"]] * 3
    path.unlink()  # Files are closed, including on Windows.


def test_transport_failure_exposes_generated_key():
    def handler(request):
        raise httpx.ConnectError("test-secret")

    with client(handler, max_retries=0) as api:
        with pytest.raises(TransportError) as caught:
            api.create_image(19, "A lighthouse")
    assert caught.value.idempotency_key
    assert "test-secret" not in str(caught.value)


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 409, 410, 413, 415])
def test_api_errors_are_not_retried(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"code": "example", "message": "test-secret"}})

    with client(handler) as api:
        with pytest.raises(APIError) as caught:
            api.me()
    assert len(calls) == 1
    assert caught.value.status_code == status
    assert caught.value.code == "example"
    assert "test-secret" not in str(caught.value)


def test_retry_after_and_non_json(monkeypatch):
    delays = []
    monkeypatch.setattr("generai.client.time.sleep", delays.append)
    responses = iter([httpx.Response(429, headers={"Retry-After": "2"}), httpx.Response(200, json={})])
    with client(lambda r: next(responses)) as api:
        api.me()
    assert delays == [2]
    with client(lambda r: httpx.Response(200, text="<html>proxy</html>")) as api:
        with pytest.raises(ProtocolError):
            api.me()


def test_redirect_is_not_followed():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": "https://other.invalid/steal"})

    with client(handler) as api:
        with pytest.raises(APIError):
            api.me()
    assert len(calls) == 1


def test_polling_waits_through_100_percent_and_unknown_stages():
    states = iter(["processing", "upscaling", "soundprocessing", "future-stage", "done"])
    updates = []
    with client(lambda r: httpx.Response(200, json={"id": 1, "status": next(states), "progress": 100})) as api:
        assert api.wait(1, poll_interval=0.001, on_update=updates.append)["status"] == "done"
    assert len(updates) == 5


@pytest.mark.parametrize("state", ["failed", "cancelled"])
def test_failed_job_retains_refund(state):
    with client(lambda r: httpx.Response(200, json={"id": 1, "status": state, "refunded": "16.00"})) as api:
        with pytest.raises(JobFailed) as caught:
            api.wait(1)
        assert caught.value.job["refunded"] == "16.00"
        assert api.wait(1, raise_on_failure=False)["status"] == state


def test_timeout_does_not_cancel_remote_job():
    methods = []

    def handler(request):
        methods.append(request.method)
        return httpx.Response(200, json={"id": 1, "status": "pending"})

    with client(handler) as api:
        with pytest.raises(PollingTimeout) as caught:
            api.wait(1, timeout=0.01, poll_interval=0.002)
    assert caught.value.job_id == 1
    assert set(methods) == {"GET"}


def test_history_pages():
    def handler(request):
        page = int(request.url.params["page"])
        return httpx.Response(200, json={"items": [{"id": page}] if page < 3 else [], "page_size": 1})

    with client(handler) as api:
        assert list(api.iter_generations()) == [{"id": 1}, {"id": 2}]
        assert list(api.iter_generations(max_pages=1)) == [{"id": 1}]


def test_download_streaming_filename_and_no_clobber(tmp_path):
    data = b"example image bytes"
    with client(lambda r: httpx.Response(200, content=data, headers={
        "Content-Type": "image/png", "Content-Disposition": 'attachment; filename="../../evil.png"',
    })) as api:
        path = api.download(3, tmp_path)
        assert path == tmp_path / "generation-3.png"
        assert path.read_bytes() == data
        with pytest.raises(FileExistsError):
            api.download(3, tmp_path)
        api.download(3, tmp_path, overwrite=True)
    assert len(list(tmp_path.iterdir())) == 1


def test_failed_stream_leaves_no_partial_file(tmp_path):
    class Broken(httpx.SyncByteStream):
        def __iter__(self):
            yield b"part"
            raise httpx.ReadError("disconnected")

    with client(lambda r: httpx.Response(200, stream=Broken())) as api:
        with pytest.raises(TransportError):
            api.download(1, tmp_path / "result.mp4")
    assert list(tmp_path.iterdir()) == []


def test_threaded_submissions_keep_independent_keys():
    def handler(request):
        return httpx.Response(202, json={"job_id": 1})

    with client(handler) as api, ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda i: api.create_image(19, f"Landscape {i}"), range(8)))
    assert len({r["idempotency_key"] for r in results}) == 8


def test_invalid_arguments_do_not_send():
    def handler(request):
        pytest.fail("Invalid arguments reached network")

    with client(handler) as api:
        for job_id in [True, 0, -1, "1/../../me"]:
            with pytest.raises(ValueError):
                api.generation(job_id)
        with pytest.raises(ValueError):
            api.create_image(19, "test", idempotency_key="bad key")
        with pytest.raises(ValueError):
            api.quote("safe", 1, is_long=float("nan"))
        with pytest.raises(ValueError):
            api.create_video("edit", 1)
    with pytest.raises(ValueError):
        GenerAI("x", base_url="https://example.com/api/v1")
    assert new_idempotency_key() != new_idempotency_key()


def test_download_length_mismatch_preserves_existing_file(tmp_path):
    target = tmp_path / "result.mp4"
    target.write_bytes(b"original")
    with client(lambda r: httpx.Response(200, content=b"short", headers={"Content-Length": "50"})) as api:
        with pytest.raises(ProtocolError):
            api.download(1, target, overwrite=True)
    assert target.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [target]


def test_file_limits_and_invalid_upload_fields(tmp_path):
    file = tmp_path / "image.png"
    file.write_bytes(b"")
    with client(lambda r: pytest.fail("must not send")) as api:
        with pytest.raises(ValueError):
            api.edit_image(1, file)
        with pytest.raises(ValueError):
            api.create("edit", 1, files={"private_workflow": file})
        with pytest.raises(FileNotFoundError):
            api.edit_image(1, tmp_path / "missing.png")
        with file.open("wb") as handle:
            handle.truncate(10_000_001)
        with pytest.raises(ValueError):
            api.edit_image(1, file)


def test_video_upload_and_options(tmp_path):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"video-fixture")

    def handler(request):
        assert b'name="input_video"; filename="source.mp4"' in request.content
        assert b"Content-Type: video/mp4" in request.content
        assert b"video-fixture" in request.content
        assert b'name="minimax_duration"' in request.content
        return httpx.Response(202, json={"job_id": 4})

    with client(handler) as api:
        assert api.create_video("nsfw", 286, files={"input_video": video},
                                minimax_duration=5)["job_id"] == 4


def test_no_clobber_when_two_downloads_target_same_path(tmp_path):
    from threading import Barrier

    barrier = Barrier(2)

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            barrier.wait(timeout=5)
            yield b"complete file"

    def run(api):
        try:
            return api.download(1, tmp_path / "result.png")
        except FileExistsError:
            return "exists"

    with client(lambda r: httpx.Response(200, stream=Stream())) as api:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: run(api), range(2)))
    assert results.count("exists") == 1
    assert (tmp_path / "result.png").read_bytes() == b"complete file"
    assert len(list(tmp_path.iterdir())) == 1
