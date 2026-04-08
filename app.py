from flask import Flask, request, jsonify
import os
from werkzeug.utils import secure_filename
from converter import process_input

app = Flask(__name__)

SAVE_DIR = "/home/Phil/Downloads/DirtyGobbler/"
TEMP_DIR = "/home/Phil/projects/DirtyGobbler/temp/"

for d in [SAVE_DIR, TEMP_DIR]:
    if not os.path.exists(d):
        os.makedirs(d)

@app.route('/')
def index():
    return app.send_static_file('index.html')

@app.route('/process', methods=['POST'])
def process():
    if 'file' in request.files:
        file = request.files['file']
        if file.filename == '':
            return jsonify({"status": "error", "message": "No file selected"}), 400
        
        filename = secure_filename(file.filename)
        temp_path = os.path.join(TEMP_DIR, filename)
        file.save(temp_path)
        
        raw, cleaned = process_input(temp_path, is_url=False)

        if os.path.exists(temp_path):
            os.remove(temp_path)
    else:
        data = request.json
        source = data.get('source')
        depth = int(data.get('depth', 1))
        raw, cleaned = process_input(source, is_url=True, depth=depth)

    if raw.startswith("Error"):
        return jsonify({"status": "error", "message": raw}), 400

    return jsonify({"status": "success", "raw": raw, "content": cleaned})

@app.route('/download', methods=['POST'])
def download():
    data = request.json
    filename = data.get('filename')
    content = data.get('content')
    
    if not os.path.exists(SAVE_DIR):
        os.makedirs(SAVE_DIR)
        
    save_path = os.path.join(SAVE_DIR, filename)
    
    try:
        with open(save_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({"status": "success", "path": save_path})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5001)
