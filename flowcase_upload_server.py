import argparse
import base64
import os
from flask import Flask, request, make_response

app = Flask(__name__)

parser = argparse.ArgumentParser()
parser.add_argument("--ssl", action="store_true")
parser.add_argument("--auth-token")
import zipfile
import io

parser.add_argument("--port", default="4902")
parser.add_argument("--upload-dir", default=(os.path.join(os.getenv("HOME"), "Shared")))
args, _ = parser.parse_known_args()

def check_auth(request):
	if args.auth_token != "":
		if "Authorization" in request.headers and request.headers["Authorization"].startswith("Basic "):
			try:
				user_pass_raw = base64.b64decode(request.headers["Authorization"].replace("Basic ", ""))
				user_pass_as_text = user_pass_raw.decode("ISO-8859-1")
				if user_pass_as_text != args.auth_token:
					return False
				return True
			except:
				return False
		return False
	return True

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

	file = request.files["file"]
	current_chunk = int(request.form["dzchunkindex"])
	filepath = request.form.get("filepath", file.filename)

	if args.upload_dir:
		os.makedirs(args.upload_dir, exist_ok=True)
		parts = filepath.replace('\\', '/').split('/')
		escaped_parts = [escapeFilename(p) for p in parts if p]
		safe_filepath = os.path.join(*escaped_parts) if escaped_parts else escapeFilename(file.filename)
		
		save_path_final = os.path.join(args.upload_dir, safe_filepath)
		save_path = os.path.join(args.upload_dir, "." + safe_filepath.replace('/', '_').replace('\\', '_') + ".uploading")
		
		os.makedirs(os.path.dirname(save_path_final), exist_ok=True)
		os.makedirs(os.path.dirname(save_path), exist_ok=True)
		
		if os.path.exists(save_path_final):
			if current_chunk == 0:
				return make_response(('File already exists', 400))
		if os.path.exists(save_path) and current_chunk == 0:
			os.remove(save_path)
	else:
		return make_response(("Couldn't find upload path", 500))

	if current_chunk == 0:
		syssize = os.statvfs(args.upload_dir)
		space = syssize.f_bsize * syssize.f_bavail
		if space - int(request.form["dztotalfilesize"]) < 0:
			return make_response(('No Space available', 400))
	try:
		with open(save_path, "ab") as f:
			f.seek(int(request.form["dzchunkbyteoffset"]))
			f.write(file.stream.read())
	except OSError:
		return make_response(("Couldn't write the file to disk", 500))
	else:
		total_chunks = int(request.form["dztotalchunkcount"])
		if current_chunk + 1 == total_chunks:
			if os.path.getsize(save_path) != int(request.form["dztotalfilesize"]):
				os.remove(save_path)
				return make_response(('Size mismatch', 500))
			os.rename(save_path, save_path_final)
		return make_response(('uploaded Chunk', 200))

def escapeFilename(filename):
	keepcharacters = (' ', '.', '_', '-')
	return "".join((c for c in filename if c.isalnum() or c in keepcharacters)).rstrip()

if __name__ == "__main__":
	app.run(host="0.0.0.0", port=args.port, ssl_context="adhoc" if args.ssl else None)