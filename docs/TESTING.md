# Testing

## Offline tests

```shell
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check .
python -m build
```

Tests cover routes, text-only multipart, complete upload retries with the original key, HTTP/network/HTML failures, Retry-After, redirect rejection, intermediate states at 100% progress, polling deadlines, history, concurrent calls, safe filenames, download races, and partial-file cleanup.

## Production: free requests

Set the API key in your environment, then run in PowerShell:

```powershell
$env:GENERAI_LIVE_TESTS = "1"
python -m pytest tests/test_live.py -q -s
```

## Production: one paid smoke test

Additionally set:

```powershell
$env:GENERAI_LIVE_PAID = "1"
$env:GENERAI_TEST_CATEGORY_ID = "19"
python -m pytest tests/test_live.py -q -s
```

Choose a valid txt2img category ID from your catalog. The test creates one image, replays the same request with the same key, waits for completion, and downloads it. The quoted price is compared to the actual cost; a server pricing change between those requests can fail this assertion. Each new test run creates a new job. Unset the variables after testing.

## Build and install a wheel

```shell
python -m build
python -m venv wheel-test
# Activate the new environment, then:
python -m pip install dist/generai_python-0.1.0-py3-none-any.whl
python -c "import generai; print(generai.__version__)"
```
