from flask import Flask

from .items import admin, bp

app = Flask(__name__)
app.register_blueprint(bp, url_prefix="/v2/items")       # replaces the blueprint's own /items
app.register_blueprint(admin)


@app.route("/ping")
def ping():
    return "pong"


def status():
    return {"up": True}


app.add_url_rule("/status", view_func=status, methods=["GET"])
