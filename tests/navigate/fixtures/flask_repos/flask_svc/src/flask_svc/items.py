from flask import Blueprint

bp = Blueprint("items", __name__, url_prefix="/items")
admin = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/<int:item_id>", methods=["GET", "DELETE"])
def item(item_id):
    return {"id": item_id}


@bp.get("/search")
def search():
    return {"hits": []}


@admin.post("/reset")
def reset():
    return {"ok": True}
