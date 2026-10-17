import os
from urllib.parse import urljoin

import requests

BASE = os.environ.get("ITEMS_URL", "http://items.internal:5000")
session = requests.Session()


def get_item(item_id):
    return requests.get(f"{BASE}/v2/items/{item_id}", timeout=5).json()


def delete_item(item_id):
    return session.delete("%s/v2/items/%s" % (BASE, item_id))


def search(q):
    return requests.get(BASE + "/v2/items/search?q=" + q)


def reset():
    return requests.request("POST", urljoin(BASE, "/admin/reset"))


def ping():
    return requests.get(BASE + "/ping")


def status():
    return requests.get("{}/status".format(BASE))


def unrelated(d):
    return d.get("/v2/items/5")        # dict.get on a string key, not an HTTP call
