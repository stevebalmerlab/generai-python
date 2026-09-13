"""Opt-in tests. Normal pytest never contacts production or spends credits."""

import os

import pytest

from generai import GenerAI, credits, new_idempotency_key

pytestmark = pytest.mark.skipif(
    os.getenv("GENERAI_LIVE_TESTS") != "1", reason="Set GENERAI_LIVE_TESTS=1 to test production"
)


def test_live_reads_quote_and_openapi():
    with GenerAI() as api:
        assert api.me()["premium"] is True
        categories = api.categories(model="txt2img")
        assert categories
        category = api.category("txt2img", categories[0]["id"])
        quote = api.quote("txt2img", category["id"], user_prompt="A lighthouse at sunset")
        assert credits(quote["cost"]) > 0
        assert api.openapi()["openapi"].startswith("3.")
        assert isinstance(api.generations()["items"], list)


@pytest.mark.skipif(os.getenv("GENERAI_LIVE_PAID") != "1", reason="Paid smoke test opt-in")
def test_live_paid_generation_and_replay(tmp_path):
    category_id = int(os.environ["GENERAI_TEST_CATEGORY_ID"])
    with GenerAI() as api:
        key = new_idempotency_key()
        prompt = "A lighthouse on a rocky coast at sunset, landscape photograph"
        quote = api.quote("txt2img", category_id, user_prompt=prompt)
        created = api.create_image(category_id, prompt, idempotency_key=key)
        print(f"Created job {created['job_id']}; idempotency key: {key}")
        replay = api.create_image(category_id, prompt, idempotency_key=key)
        assert replay["job_id"] == created["job_id"] and replay["replayed"]
        final = api.wait(created["job_id"], timeout=1800)
        assert credits(final["cost"]) == credits(quote["cost"])
        assert api.download(created["job_id"], tmp_path).stat().st_size > 0
