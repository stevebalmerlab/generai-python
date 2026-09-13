# SDK reference

## Constructor

```python
GenerAI(api_key=None, base_url=None, timeout=120.0,
        max_retries=2, retry_delay=1.0, transport=None)
```

Credentials default to `GENERAI_API_KEY`; the origin defaults to `GENERAI_BASE_URL` or `https://generai.org`. `transport` supports HTTP testing. TLS verification is enabled. Redirects are not followed, so a redirect cannot send the key to another origin. Avoid logging secret HTTP headers in your application.

`max_retries=2` allows up to **three attempts**. Network failures and HTTP 429/500/502/503/504 are retried. HTTP 400/401/402/403/404/409/410/413/415 are not. `Retry-After` is respected with a 60-second cap. A broken download stream raises an error and deletes the temporary file; call `download()` again to retry.

## Catalog and options

```python
items = api.categories(model="minimax_h3", category_type="nsfw")
category = api.category("nsfw", category_id)
```

Category types: `txt2img`, `edit`, `safe`, `nsfw`. Models: `txt2img`, `edit`, `wan22`, `minimax_h3`.

Inspect the current `inputs`, `options`, and `default_options`. Missing capability fields do not grant permission. Select relevant request fields rather than blindly forwarding the whole catalog object. Pricing depends on the category, options, and account. The SDK does not cache prices or capabilities.

## Uploads

```python
job = api.create("nsfw", category_id, files={
    "input_image": "first.png",
    "input_image_2": "second.jpg",
    "input_image_3": "third.webp",
    "input_video": "reference.mp4",
}, **selected_options)
```

This lists possible fields, **not a universally valid combination**. Use only the selected category's inputs. Uploads accept local `str`/`Path` filenames, not URLs, byte strings, or open streams. Handles are closed after each attempt. Maximum image size: 10,000,000 bytes; video: 16,000,000 bytes. The SDK checks existence and size; the server validates actual media, dimensions, and duration. Quotes do not validate files.

## MiniMax modes and duration

```python
options = {
    "minimax_generation_mode": "turbo",
    "minimax_duration": 5,
    "minimax_turbo_resolution": "hq",
}
price = api.quote("nsfw", category_id, **options)
job = api.create_video("nsfw", category_id,
    files={"input_image": "photo.png"}, **options)
```

Choose values from `generation_modes` and `duration_seconds`/`turbo_duration_seconds`. `forced_duration` fixes the duration. HQ requires Turbo and category support.

## References

- Two mandatory images: `input_image` and `input_image_2`.
- Replace a built-in second image: `ref2_source="custom"` plus `input_image_2`, only when `inputs.replace_second_image=True`.
- Replace a built-in third image: `ref3_source="custom"` plus `input_image_3`, when allowed; other mandatory images are still required.
- Video-only input: `input_video` when `inputs.video.required=True`; do not add images to a category that does not accept them.
- Replace a built-in video: `ref_video_source="custom"` plus `input_video`, only when `inputs.replace_video=True`.

## Dialogue editing

```python
category = api.category("nsfw", category_id)
print(category["options"].get("dialogues"))
job = api.create_video("nsfw", category_id,
    files={"input_image": "first.png", "input_image_2": "second.png"},
    dialogue_language="en", dialogue_edits={0: "I like this green shirt."},
    minimax_generation_mode="turbo", minimax_duration=5)
```

Indexes are zero-based positions in `options.dialogues[language]`. A Python mapping passed as `dialogue_edits` is serialized to a JSON string. Use `remove_dialogues=True` to remove dialogue. Avoid conflicting operations; full prompt replacement and scenario rewriting can replace the original text. An audio track does not guarantee that a generative model pronounces the requested text verbatim.

## WAN and image editing

- `is_long=True`: WAN categories with `long_video=True` only.
- `is_pingpong=True`: requires `pingpong_available=True`.
- `is_face_repair=True`: input-image enhancement when `face_repair_available=True`.
- `is_upscale_cinematic=True`: also requires `is_face_repair=True`.
- `skip_preprocess=True`: supported categories only; check the category's input contract.
- `enhance_prompt=True`: supported prompt-enhancement categories only.
- `is_nsfw`, `is_strong`, `edit_engine`, `width/height`, and `realistic_filter/amateur_filter` apply to their respective image/editing operations, not video.

## Polling and resuming

`pending`, `processing`, `soundprocessing`, and `upscaling` are intermediate states. `progress=100` does not mean the result is ready. The SDK waits for `done`, `failed`, or `cancelled`; unknown intermediate states also keep polling.

`wait(..., raise_on_failure=False)` returns failed terminal responses. By default it raises `JobFailed` with the response in `.job`. The callback receives every status; callback exceptions propagate to the caller. Polling timeouts do not cancel jobs. Resume after restarting your application using the saved job ID with `wait()` or `download()`.

The HTTP timeout is capped to the remaining polling time, but applies to individual network operations. This is not a hard wall-clock deadline for a slowly streaming response or user callbacks.

## Downloads

```python
api.download(job_id, "downloads")             # directory; name derived from job ID and MIME
api.download(job_id, "results/final.mp4")      # explicit filename
api.download(job_id, "results/final.mp4", overwrite=True)
```

A nonexistent suffix-less destination is treated as a directory. Data is streamed into a neighboring `.part` file and published atomically only after completion. Without `overwrite`, existing files are never replaced, including concurrent downloads. Atomic no-clobber publication requires hard-link support (available on normal NTFS/ext4 local filesystems); filesystem failures propagate as `OSError`. Server filenames and `result_url` are not trusted as local paths or download URLs. Download results promptly: server retention is limited and expired results return `410`.

## Exceptions

| Exception | Attributes |
|---|---|
| `APIError` | `status_code`, `code`, `message`, `idempotency_key` |
| `TransportError` | `idempotency_key` for creation; acceptance may be unknown |
| `ProtocolError` | `idempotency_key` for an unexpected creation response |
| `JobFailed` | `job`, including status, `error_code`, and `refunded` |
| `PollingTimeout` | `job_id`, `last_status` |

All inherit from `GenerAIError`. Argument/filesystem errors use standard `ValueError`, `FileNotFoundError`, `FileExistsError`, and `OSError`. Server messages may be localized; branch on error codes rather than human-readable text. The SDK preserves server-provided localized content.

## Idempotency

Persist the key, fields, and immutable input files before submitting. Retry the same payload with the same key. Changed files or parameters produce `409`. Automatic retries reuse the original key. If omitted, the SDK generates a key and includes it in the result or transport/protocol exception. For recovery after a process crash, explicitly supply a previously persisted key.
