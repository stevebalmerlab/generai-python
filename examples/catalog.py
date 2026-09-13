"""Free requests: python examples/catalog.py"""
from generai import GenerAI, credits

with GenerAI() as api:
    account = api.me()
    print("Balance:", credits(account["balance"]))
    for model in ("txt2img", "edit", "wan22", "minimax_h3"):
        items = api.categories(model=model)
        print(model, len(items))
        for item in items[:3]:
            print(item["id"], item["title"], item["inputs"], item["options"])
