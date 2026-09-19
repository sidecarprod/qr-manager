import io
import os
import secrets
import sqlite3
import string
from datetime import datetime, timezone
from functools import wraps

import qrcode
import qrcode.image.svg
from flask import (
    Flask, request, redirect, render_template, session, url_for,
    send_file, abort, g, flash, jsonify
)

DB_PATH = os.environ.get("QR_DB_PATH", "/data/qr.db")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000").rstrip("/")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")
SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
API_KEY = os.environ.get("API_KEY")  # unset = API disabled entirely

app = Flask(__name__)
app.secret_key = SECRET_KEY

ALPHABET = string.ascii_lowercase + string.digits
CODE_LENGTH = 7


# ---------------------------------------------------------------- database

def get_db():
    if "db" not in g:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            label TEXT,
            target_url TEXT NOT NULL,
            scan_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def generate_code(db):
    while True:
        candidate = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
        exists = db.execute(
            "SELECT 1 FROM codes WHERE code = ?", (candidate,)
        ).fetchone()
        if not exists:
            return candidate


# ---------------------------------------------------------------- auth

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("authed"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def api_key_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not API_KEY:
            return jsonify(error="API access is disabled (no API_KEY configured)"), 403
        supplied = request.headers.get("X-API-Key", "")
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            supplied = supplied or auth_header[len("Bearer "):]
        if not supplied or not secrets.compare_digest(supplied, API_KEY):
            return jsonify(error="Invalid or missing API key"), 401
        return view(*args, **kwargs)
    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if secrets.compare_digest(request.form.get("password", ""), ADMIN_PASSWORD):
            session["authed"] = True
            return redirect(request.args.get("next") or url_for("index"))
        flash("Incorrect password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


class CodeCreationError(Exception):
    def __init__(self, message):
        self.message = message


def create_code_record(db, target_url, label="", custom_code=""):
    target_url = (target_url or "").strip()
    label = (label or "").strip()
    custom_code = (custom_code or "").strip().lower()

    if not target_url:
        raise CodeCreationError("target_url is required.")

    if custom_code:
        allowed = set(ALPHABET + "-_")
        if not set(custom_code) <= allowed:
            raise CodeCreationError("custom_code can only contain lowercase letters, numbers, - and _.")
        exists = db.execute("SELECT 1 FROM codes WHERE code = ?", (custom_code,)).fetchone()
        if exists:
            raise CodeCreationError(f"Code '{custom_code}' is already taken.")
        code = custom_code
    else:
        code = generate_code(db)

    ts = now_iso()
    cur = db.execute(
        "INSERT INTO codes (code, label, target_url, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (code, label, target_url, ts, ts),
    )
    db.commit()
    return db.execute("SELECT * FROM codes WHERE id = ?", (cur.lastrowid,)).fetchone()


def serialize_code(row):
    return {
        "id": row["id"],
        "code": row["code"],
        "label": row["label"],
        "target_url": row["target_url"],
        "scan_count": row["scan_count"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "redirect_url": build_qr_url(row["code"]),
        "png_url": f"{BASE_URL}{url_for('qr_png', code_id=row['id'])}",
        "svg_url": f"{BASE_URL}{url_for('qr_svg', code_id=row['id'])}",
    }


# ---------------------------------------------------------------- redirect (public)

@app.route("/r/<code>")
def resolve(code):
    db = get_db()
    row = db.execute("SELECT * FROM codes WHERE code = ?", (code,)).fetchone()
    if not row:
        abort(404)
    db.execute(
        "UPDATE codes SET scan_count = scan_count + 1 WHERE id = ?", (row["id"],)
    )
    db.commit()
    return redirect(row["target_url"], code=302)


# ---------------------------------------------------------------- library UI

@app.route("/")
@login_required
def index():
    db = get_db()
    q = request.args.get("q", "").strip()
    if q:
        rows = db.execute(
            "SELECT * FROM codes WHERE label LIKE ? OR target_url LIKE ? OR code LIKE ? "
            "ORDER BY created_at DESC",
            (f"%{q}%", f"%{q}%", f"%{q}%"),
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM codes ORDER BY created_at DESC").fetchall()
    return render_template("index.html", codes=rows, base_url=BASE_URL, q=q)


@app.route("/new", methods=["GET", "POST"])
@login_required
def new():
    if request.method == "POST":
        target_url = request.form.get("target_url", "").strip()
        label = request.form.get("label", "").strip()
        custom_code = request.form.get("custom_code", "").strip().lower()
        db = get_db()
        try:
            create_code_record(db, target_url, label, custom_code)
        except CodeCreationError as e:
            flash(e.message, "error")
            return render_template("new.html", target_url=target_url, label=label, custom_code=custom_code)
        flash("QR code created.", "success")
        return redirect(url_for("index"))

    return render_template("new.html", target_url="", label="", custom_code="")


@app.route("/edit/<int:code_id>", methods=["GET", "POST"])
@login_required
def edit(code_id):
    db = get_db()
    row = db.execute("SELECT * FROM codes WHERE id = ?", (code_id,)).fetchone()
    if not row:
        abort(404)

    if request.method == "POST":
        target_url = request.form.get("target_url", "").strip()
        label = request.form.get("label", "").strip()
        if not target_url:
            flash("Target URL is required.", "error")
            return render_template("edit.html", code=row)
        db.execute(
            "UPDATE codes SET target_url = ?, label = ?, updated_at = ? WHERE id = ?",
            (target_url, label, now_iso(), code_id),
        )
        db.commit()
        flash("Updated — the QR image is unchanged.", "success")
        return redirect(url_for("index"))

    return render_template("edit.html", code=row)


@app.route("/delete/<int:code_id>", methods=["POST"])
@login_required
def delete(code_id):
    db = get_db()
    db.execute("DELETE FROM codes WHERE id = ?", (code_id,))
    db.commit()
    flash("Deleted.", "success")
    return redirect(url_for("index"))


# ---------------------------------------------------------------- QR export

def build_qr_url(code):
    return f"{BASE_URL}/r/{code}"


@app.route("/qr/<int:code_id>.png")
@login_required
def qr_png(code_id):
    db = get_db()
    row = db.execute("SELECT * FROM codes WHERE id = ?", (code_id,)).fetchone()
    if not row:
        abort(404)
    img = qrcode.make(build_qr_url(row["code"]), box_size=10, border=4)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    filename = f"{row['label'] or row['code']}.png".replace(" ", "_")
    return send_file(buf, mimetype="image/png", as_attachment=True, download_name=filename)


@app.route("/qr/<int:code_id>.svg")
@login_required
def qr_svg(code_id):
    db = get_db()
    row = db.execute("SELECT * FROM codes WHERE id = ?", (code_id,)).fetchone()
    if not row:
        abort(404)
    factory = qrcode.image.svg.SvgPathImage
    img = qrcode.make(build_qr_url(row["code"]), image_factory=factory, box_size=10, border=4)
    buf = io.BytesIO()
    img.save(buf)
    buf.seek(0)
    filename = f"{row['label'] or row['code']}.svg".replace(" ", "_")
    return send_file(buf, mimetype="image/svg+xml", as_attachment=True, download_name=filename)


@app.route("/qr/<int:code_id>/preview.png")
@login_required
def qr_preview(code_id):
    db = get_db()
    row = db.execute("SELECT * FROM codes WHERE id = ?", (code_id,)).fetchone()
    if not row:
        abort(404)
    img = qrcode.make(build_qr_url(row["code"]), box_size=6, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


# ---------------------------------------------------------------- API

@app.route("/api/codes", methods=["GET"])
@api_key_required
def api_list_codes():
    db = get_db()
    rows = db.execute("SELECT * FROM codes ORDER BY created_at DESC").fetchall()
    return jsonify(codes=[serialize_code(r) for r in rows])


@app.route("/api/codes/<int:code_id>", methods=["GET"])
@api_key_required
def api_get_code(code_id):
    db = get_db()
    row = db.execute("SELECT * FROM codes WHERE id = ?", (code_id,)).fetchone()
    if not row:
        return jsonify(error="Not found"), 404
    return jsonify(serialize_code(row))


@app.route("/api/codes", methods=["POST"])
@api_key_required
def api_create_code():
    data = request.get_json(silent=True) or {}
    db = get_db()
    try:
        row = create_code_record(
            db,
            target_url=data.get("target_url", ""),
            label=data.get("label", ""),
            custom_code=data.get("custom_code", ""),
        )
    except CodeCreationError as e:
        return jsonify(error=e.message), 400
    return jsonify(serialize_code(row)), 201


@app.route("/api/codes/bulk", methods=["POST"])
@api_key_required
def api_bulk_create_codes():
    """
    Body: {"codes": [{"target_url": "...", "label": "...", "custom_code": "..."}, ...]}
    label and custom_code are optional per item. Processes every item even if
    some fail; each result reports success or an error for its own index so a
    partial batch never gets silently dropped.
    """
    data = request.get_json(silent=True) or {}
    items = data.get("codes")
    if not isinstance(items, list) or not items:
        return jsonify(error="Body must be {\"codes\": [ {target_url, label?, custom_code?}, ... ]}"), 400
    if len(items) > 500:
        return jsonify(error="Max 500 codes per bulk request."), 400

    db = get_db()
    results = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            results.append({"index": i, "success": False, "error": "Item must be an object."})
            continue
        try:
            row = create_code_record(
                db,
                target_url=item.get("target_url", ""),
                label=item.get("label", ""),
                custom_code=item.get("custom_code", ""),
            )
            results.append({"index": i, "success": True, "code": serialize_code(row)})
        except CodeCreationError as e:
            results.append({"index": i, "success": False, "error": e.message})

    created = sum(1 for r in results if r["success"])
    return jsonify(created=created, failed=len(results) - created, results=results), 207


@app.route("/api/codes/<int:code_id>", methods=["PATCH"])
@api_key_required
def api_update_code(code_id):
    db = get_db()
    row = db.execute("SELECT * FROM codes WHERE id = ?", (code_id,)).fetchone()
    if not row:
        return jsonify(error="Not found"), 404
    data = request.get_json(silent=True) or {}
    target_url = data.get("target_url", row["target_url"]).strip()
    label = data.get("label", row["label"] or "").strip()
    if not target_url:
        return jsonify(error="target_url cannot be empty."), 400
    db.execute(
        "UPDATE codes SET target_url = ?, label = ?, updated_at = ? WHERE id = ?",
        (target_url, label, now_iso(), code_id),
    )
    db.commit()
    row = db.execute("SELECT * FROM codes WHERE id = ?", (code_id,)).fetchone()
    return jsonify(serialize_code(row))


@app.route("/api/codes/<int:code_id>", methods=["DELETE"])
@api_key_required
def api_delete_code(code_id):
    db = get_db()
    row = db.execute("SELECT * FROM codes WHERE id = ?", (code_id,)).fetchone()
    if not row:
        return jsonify(error="Not found"), 404
    db.execute("DELETE FROM codes WHERE id = ?", (code_id,))
    db.commit()
    return jsonify(success=True)


@app.route("/api/codes/<int:code_id>/qr.png", methods=["GET"])
@api_key_required
def api_qr_png(code_id):
    return qr_png.__wrapped__(code_id)


@app.route("/api/codes/<int:code_id>/qr.svg", methods=["GET"])
@api_key_required
def api_qr_svg(code_id):
    return qr_svg.__wrapped__(code_id)


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
