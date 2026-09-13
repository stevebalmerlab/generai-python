"""Paid generation: python examples/generate.py txt2img 19 --prompt "A lighthouse".

For video/edit use --image and optionally --second-image / --third-image / --video.
Pass model-specific parameters with --options '{"minimax_duration":5}'.
"""
import argparse
import json

from generai import GenerAI, new_idempotency_key

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("category_type", choices=["txt2img", "edit", "safe", "nsfw"])
parser.add_argument("category_id", type=int)
parser.add_argument("--prompt")
parser.add_argument("--full-prompt")
parser.add_argument("--image")
parser.add_argument("--second-image")
parser.add_argument("--third-image")
parser.add_argument("--video")
parser.add_argument("--options", default="{}", help="JSON object of model-specific options")
parser.add_argument("--idempotency-key", help="Reuse ONLY to retry the same job")
parser.add_argument("--output", default="downloads")
args = parser.parse_args()
options = json.loads(args.options)
if args.prompt is not None:
    options["user_prompt"] = args.prompt
if args.full_prompt is not None:
    options["full_prompt"] = args.full_prompt
files = {name: path for name, path in {
    "input_image": args.image, "input_image_2": args.second_image,
    "input_image_3": args.third_image, "input_video": args.video,
}.items() if path}
key = args.idempotency_key or new_idempotency_key()
print("Save this idempotency key before retrying:", key, flush=True)
with GenerAI() as api:
    print("Quote:", api.quote(args.category_type, args.category_id, **options), flush=True)
    job = api.create(args.category_type, args.category_id, files=files, idempotency_key=key, **options)
    print("Created:", job, flush=True)
    api.wait(job["job_id"], on_update=lambda status: print(
        status["status"], status.get("progress"), flush=True
    ))
    print("Saved:", api.download(job["job_id"], args.output))
