import argparse
import base64
import os
import shutil
import zipfile
import io
from flask import Flask, request, make_response, jsonify

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 1024 * 16  # 16 GB

parser = argparse.ArgumentParser()
parser.add_argument("--ssl", action="store_true")
parser.add_argument("--auth-token")
parser.add_argument("--port", default="4902")

# Accept both --upload-dir (canonical) and --upload_dir (legacy vnc_startup.sh form)
parser.add_argument("--upload-dir", dest="upload_dir",
                    default=os.path.join(os.getenv("HOME", "/tmp"), "Shared"))
parser.add_argument("--upload_dir", dest="upload_dir_legacy",
                    default=None)

args, _ = parser.parse_known_args()

# If the underscore form was passed (from vnc_startup.sh), prefer it
if args.upload_dir_legacy is not None:
    args.upload_dir = args.upload_dir_legacy

# Ensure the upload directory exists at startup
os.makedirs(args.upload_dir, exist_ok=True)


def check_auth(req):
    if args.auth_token and args.auth_token != "":
        if "Authorization" in req.headers and req.headers["Authorization"].startswith("Basic "):
            try:
                user_pass_raw = base64.b64decode(req.headers["Authorization"].replace("Basic ", ""))
                user_pass_as_text = user_pass_raw.decode("ISO-8859-1")
                if user_pass_as_text != args.auth_token:
                    return False
                return True
            except Exception:
                return False
        return False
    return True


def get_free_space(path):
    """Return free bytes available at path, cross-platform."""
    try:
        usage = shutil.disk_usage(path)
        return usage.free
    except Exception:
        return None


def escapeFilename(filename):
    keepcharacters = (' ', '.', '_', '-')
    return "".join((c for c in filename if c.isalnum() or c in keepcharacters)).rstrip()


@app.route("/status", methods=["GET"])
def status():
    """Health-check endpoint."""
    free = get_free_space(args.upload_dir)
    return jsonify({
        "ok": True,
        "upload_dir": args.upload_dir,
        "free_bytes": free,
    })


@app.route("/download", methods=["GET"])
def download():
    if not check_auth(request):
        return make_response(('Access Denied', 403))

    rel_path = request.args.get('path', '')
    if '..' in rel_path or rel_path.startswith('/'):
        return make_response(('Invalid path', 400))

    target_path = os.path.join(args.upload_dir, rel_path)
    if not os.path.exists(target_path):
        return make_response(('Not found', 404))

    if os.path.isfile(target_path):
        from flask import send_file
        return send_file(target_path, as_attachment=True)
    elif os.path.isdir(target_path):
        memory_file = io.BytesIO()
        with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(target_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    archive_name = os.path.relpath(file_path, target_path)
                    zf.write(file_path, archive_name)

        folder_name = os.path.basename(os.path.normpath(target_path)) or "archive"
        response = make_response(memory_file.getvalue())
        response.headers['Content-Type'] = 'application/zip'
        response.headers['Content-Disposition'] = f'attachment; filename={folder_name}.zip'
        return response


@app.route("/upload", methods=["POST"])
def upload():
    if not check_auth(request):
        return make_response(('Access Denied', 403))

    if "file" not in request.files:
        return make_response(("No file in request", 400))

    file = request.files["file"]
    current_chunk = int(request.form.get("dzchunkindex", 0))
    filepath = request.form.get("filepath", file.filename)

    if not args.upload_dir:
        return make_response(("Couldn't find upload path", 500))

    # Ensure upload dir exists (may have been deleted after startup)
    os.makedirs(args.upload_dir, exist_ok=True)

    parts = filepath.replace('\\', '/').split('/')
    escaped_parts = [escapeFilename(p) for p in parts if p]
    safe_filepath = os.path.join(*escaped_parts) if escaped_parts else escapeFilename(file.filename)

    save_path_final = os.path.join(args.upload_dir, safe_filepath)
    save_path = os.path.join(
        args.upload_dir,
        "." + safe_filepath.replace('/', '_').replace('\\', '_') + ".uploading"
    )

    os.makedirs(os.path.dirname(save_path_final), exist_ok=True)
    os.makedirs(os.path.dirname(save_path), exist_ok=True)

    if os.path.exists(save_path_final) and current_chunk == 0:
        return make_response(('File already exists', 400))
    if os.path.exists(save_path) and current_chunk == 0:
        os.remove(save_path)

    # Check available disk space on first chunk
    if current_chunk == 0:
        total_size = int(request.form.get("dztotalfilesize", 0))
        free = get_free_space(args.upload_dir)
        if free is not None and total_size > 0 and free < total_size:
            return make_response(('No space available', 400))

    try:
        with open(save_path, "ab") as f:
            f.seek(int(request.form.get("dzchunkbyteoffset", 0)))
            f.write(file.stream.read())
    except OSError as e:
        return make_response((f"Couldn't write file to disk: {str(e)}", 500))
    else:
        total_chunks = int(request.form.get("dztotalchunkcount", 1))
        if current_chunk + 1 == total_chunks:
            expected_size = int(request.form.get("dztotalfilesize", 0))
            actual_size = os.path.getsize(save_path)
            if expected_size > 0 and actual_size != expected_size:
                os.remove(save_path)
                return make_response((f'Size mismatch: expected {expected_size}, got {actual_size}', 500))
            os.rename(save_path, save_path_final)
        return make_response(('Chunk uploaded', 200))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=args.port, ssl_context="adhoc" if args.ssl else None)