import io
from flask import jsonify, make_response
from transformer import transform


def handler(request):
    # CORS preflight
    if request.method == "OPTIONS":
        res = make_response("", 204)
        res.headers["Access-Control-Allow-Origin"] = "*"
        res.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
        res.headers["Access-Control-Allow-Headers"] = "Content-Type"
        res.headers["Access-Control-Expose-Headers"] = "X-Warnings, Content-Disposition"
        return res

    # Health check
    if request.method == "GET":
        res = jsonify({"status": "ok"})
        res.headers["Access-Control-Allow-Origin"] = "*"
        return res

    # Transform
    if request.method != "POST":
        return jsonify({"detail": "Method not allowed"}), 405

    if "file" not in request.files:
        return jsonify({"detail": "No file uploaded. Send an xlsx as multipart/form-data field 'file'."}), 400

    file = request.files["file"]
    if not file.filename.endswith(".xlsx"):
        return jsonify({"detail": "Only .xlsx files are supported"}), 400

    try:
        contents = file.read()
        buf, warnings, _ = transform(io.BytesIO(contents))
    except ValueError as e:
        return jsonify({"detail": str(e)}), 400
    except Exception as e:
        return jsonify({"detail": f"Transform failed: {str(e)}"}), 500

    # HTTP headers are Latin-1 only — sanitize Unicode chars (em-dashes, arrows, etc.)
    warnings_safe = "||".join(warnings).encode("latin-1", errors="replace").decode("latin-1")

    response = make_response(buf.getvalue())
    response.headers["Content-Type"] = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response.headers["Content-Disposition"] = 'attachment; filename="fynd_catalog_output.xlsx"'
    response.headers["X-Warnings"] = warnings_safe
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Expose-Headers"] = "X-Warnings, Content-Disposition"
    return response
