# GenerAI Python SDK

A ready-to-use synchronous client for the GenerAI public API: image generation, image editing, WAN2.2, and MiniMax H3 video generation. **Python 3.10+** on Windows, Linux, and macOS. One runtime dependency: `httpx`.

## Installation

From this repository:

```shell
python -m pip install .
```

Install directly from GitHub (requires Git):

```shell
python -m pip install "git+https://github.com/stevebalmerlab/generai-python.git"
```

Distribution name: `generai-python`. Import name: **`generai`**. A PyPI release is not required.

## Configuration

Create an API key in your GenerAI account. API access requires Premium; generation requires credits.

PowerShell:

```powershell
$env:GENERAI_API_KEY = "YOUR_API_KEY"
$env:GENERAI_BASE_URL = "https://generai.org"
```

Bash:

```bash
export GENERAI_API_KEY="YOUR_API_KEY"
export GENERAI_BASE_URL="https://generai.org"
```

The base URL must be an origin **without `/api/v1`**. You can also pass credentials explicitly: `GenerAI(api_key="...", base_url="https://generai.org")`. The SDK does not automatically load `.env` files.

## Quick start

```python
from generai import GenerAI, credits, new_idempotency_key

with GenerAI() as api:
    print("Balance:", credits(api.me()["balance"]))
    models = api.categories(model="txt2img")
    for model in models:
        print(model["id"], model["title"], model["options"])

    category_id = models[0]["id"]  # Let your application choose the desired model.
    prompt = "A lighthouse on a rocky coast at sunset"
    print("Quote:", api.quote("txt2img", category_id, user_prompt=prompt))

    key = new_idempotency_key()
    # Persist the key and request parameters BEFORE submitting.
    created = api.create_image(category_id, prompt, idempotency_key=key)
    job_id = created["job_id"]  # Persist this to resume after a restart.
    api.wait(job_id, on_update=lambda job: print(job["status"], job.get("progress")))
    print("Saved:", api.download(job_id, "downloads"))
```

**`create*` methods incur charges.** Quotes, catalogs, history, status checks, and downloads do not create jobs. Quotes do not reserve prices; creation recalculates the current price. `credits()` converts amounts to `Decimal`.

## Available methods

| Method | Purpose |
|---|---|
| `me()` | Key details, balance, Premium status, usage |
| `openapi()` | Current server OpenAPI schema |
| `categories(model=..., category_type=...)` | Category list and capabilities |
| `category(category_type, category_id)` | One category |
| `quote(category_type, category_id, **options)` | Free cost estimate |
| `create(category_type, category_id, files=..., **options)` | Universal paid job creation |
| `create_image(category_id, prompt, **options)` | Generate an image |
| `edit_image(category_id, image, second_image=..., prompt=..., **options)` | Edit images |
| `create_video(category_type, category_id, files=..., **options)` | Generate WAN/MiniMax video |
| `generations(page=1)` | One history page for the current key |
| `iter_generations(max_pages=...)` | Iterate through history |
| `generation(job_id)` | Current job status |
| `wait(job_id, timeout=1800, poll_interval=5, on_update=...)` | Wait for a terminal state |
| `download(job_id, destination="downloads", overwrite=False)` | Stream a result to disk |
| `close()` | Close connections; automatic when using `with` |

Responses are ordinary API dictionaries, preserving new server fields. `categories()` returns the `items` list. `create*()` adds an SDK-only **`idempotency_key`** field. `download()` returns `pathlib.Path`.

The client covers the public API v1 operations listed above.

## Image editing

```python
with GenerAI() as api:
    categories = api.categories(category_type="edit")
    selected = next(c for c in categories if c["options"].get("edit_operation") == "multiref")
    job = api.edit_image(
        selected["id"], "first.png", second_image="second.png",
        prompt="Transfer the object from the second image to the first scene",
    )
    api.wait(job["job_id"])
    api.download(job["job_id"])
```

Use `edit_engine="krea2"` only when listed in `options.edit_engines`. `is_strong=True` applies only to compatible standard editing categories.

## Video and custom prompts

```python
with GenerAI() as api:
    categories = api.categories(model="wan22", category_type="safe")
    # Select the desired category and check its input requirements.
    selected = categories[0]
    job = api.create_video(
        selected["category_type"], selected["id"],
        files={"input_image": "coast.png"},
        user_prompt="The camera slowly approaches the lighthouse",
    )
    api.wait(job["job_id"])
    api.download(job["job_id"])
```

- `user_prompt`: image description or additional editing/video instructions.
- `full_prompt`: replaces the video category prompt when `options.full_prompt_available=True`. SVI/DuoFrame does not support full replacement. Do not combine it with `user_prompt`, dialogue editing/removal, or scenario rewriting.
- `change_llm_scenario=True, scenario_changes="..."`: rewrite a MiniMax scenario when the category permits it.
- Pass supported API parameters through `**options`. Python booleans become `"1"`/`"0"`; `None` values are omitted.

See **[docs/API.md](docs/API.md)** for MiniMax modes, references, dialogue editing, retries, and errors.

## Errors and retries

```python
from generai import APIError, GenerAI, JobFailed, PollingTimeout, TransportError

with GenerAI(max_retries=2) as api:
    try:
        job = api.create_image(19, "A lighthouse", idempotency_key="persisted-job-001")
        api.wait(job["job_id"], timeout=600)
    except APIError as exc:
        print(exc.status_code, exc.code, exc.message)
    except TransportError as exc:
        print("The request may have been accepted. Retry with:", exc.idempotency_key)
    except PollingTimeout as exc:
        print("Resume waiting for job:", exc.job_id)
    except JobFailed as exc:
        print(exc.job.get("error_code"), exc.job.get("refunded"))
```

Replace category ID `19` with an ID from your catalog. **A new key means a new paid request.** Automatic retries reuse one key and reopen uploaded files from the beginning. Do not modify files between attempts.

## Command-line examples

After installation and configuration:

```shell
python examples/catalog.py
python examples/generate.py txt2img 19 --prompt "A lighthouse at sunset"
python examples/generate.py edit 1 --image photo.png --prompt "Change the background"
python examples/generate.py safe 1 --image coast.png --full-prompt "A lighthouse by the ocean"
python examples/resume.py 12345
python examples/parallel.py 19
```

Choose IDs from your current catalog. `generate.py` and `parallel.py` create paid jobs. Pass additional model options as a JSON object using `--options`.

## Concurrency and asyncio

One `GenerAI` instance supports concurrent requests from multiple threads. Close it after all requests finish. See `examples/parallel.py`.

In asyncio applications, use `await asyncio.to_thread(api.wait, job_id)`. Cancelling the asyncio wrapper does not stop the thread or cancel the remote job. Set finite timeouts and wait for outstanding work before `close()`.

## Development

```shell
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check .
python -m build
```

Default tests use mocked HTTP; production access and paid tests are opt-in. See [docs/TESTING.md](docs/TESTING.md).
