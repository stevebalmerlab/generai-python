"""Paid example: two independent requests, python examples/parallel.py CATEGORY_ID"""
import argparse
from concurrent.futures import ThreadPoolExecutor

from generai import GenerAI, new_idempotency_key

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("category_id", type=int)
args = parser.parse_args()

with GenerAI() as api:
    def run(index):
        key = new_idempotency_key()
        print("Request", index, "key", key, flush=True)
        job = api.create_image(args.category_id, f"A lighthouse landscape, variation {index}",
                               idempotency_key=key)
        api.wait(job["job_id"])
        return api.download(job["job_id"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        for path in pool.map(run, (1, 2)):
            print(path)
