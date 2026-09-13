"""Resume without creating/charging: python examples/resume.py JOB_ID"""
import argparse

from generai import GenerAI

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("job_id", type=int)
parser.add_argument("--output", default="downloads")
args = parser.parse_args()
with GenerAI() as api:
    api.wait(args.job_id, on_update=lambda job: print(job["status"], job.get("progress")))
    print(api.download(args.job_id, args.output))
