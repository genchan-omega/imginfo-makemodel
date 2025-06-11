# main.py

import functions_framework
import json
from google.cloud import storage 
import os 

# Cloud Storage クライアントの初期化
storage_client = storage.Client()

# Cloud Storage バケット名
GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "your-gcs-bucket-name-if-not-set")
if GCS_BUCKET_NAME == "your-gcs-bucket-name-if-not-set":
    print("WARNING: GCS_BUCKET_NAME environment variable is not set in Cloud Functions. Using placeholder. Please set it in Cloud Build or function configuration.")

@functions_framework.http
def makemodel(request):
    """
    HTTP リクエストを受け取り、Cloud Storageから画像を読み込み、/tmpに保存し、
    その画像データ（バイナリ）をそのままNext.jsに返すCloud Function。
    """
    # CORS プリフライトリクエストへの対応
    if request.method == 'OPTIONS':
        headers = {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
            'Access-Control-Max-Age': '3600'
        }
        return ('', 204, headers)

    # レスポンスヘッダーの設定 (ここでは画像を返すためContent-Typeを動的に設定)
    response_headers = {
        'Access-Control-Allow-Origin': '*',
        # Content-Typeは動的に設定するため、ここではデフォルト値を設定しない
        # または、クライアントから渡される拡張子で推測する
        # 'Content-Type': 'application/octet-stream' # デフォルト
    }

    request_json = request.get_json(silent=True)
    received_task_id = None
    received_file_extension = None 

    if request_json:
        received_task_id = request_json.get('taskId')
        received_file_extension = request_json.get('fileExtension')
        print(f"Cloud Functions: Received JSON - taskId: {received_task_id}, fileExtension: {received_file_extension}")
    else:
        error_message = "No JSON data found in request body."
        print(f"Error: {error_message}")
        response_headers['Content-Type'] = 'application/json' # エラー時はJSONを返す
        return (json.dumps({'error': error_message}), 400, response_headers)

    if not received_task_id:
        error_message = "Missing 'taskId' in request body."
        print(f"Error: {error_message}")
        response_headers['Content-Type'] = 'application/json'
        return (json.dumps({'error': error_message}), 400, response_headers)
    
    if not received_file_extension:
        error_message = "Missing 'fileExtension' in request body."
        print(f"Error: {error_message}")
        response_headers['Content-Type'] = 'application/json'
        return (json.dumps({'error': error_message}), 400, response_headers)

    try:
        # --- GCSから画像を読み込む ---
        gcs_file_path = f"uploads/{received_task_id}.{received_file_extension}" 
        print(f"Cloud Functions: Attempting to download from GCS path: gs://{GCS_BUCKET_NAME}/{gcs_file_path}")

        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(gcs_file_path)

        if not blob.exists():
            error_message = f"File not found in GCS: gs://{GCS_BUCKET_NAME}/{gcs_file_path}. Please check filename or upload status."
            print(f"Error: {error_message}")
            response_headers['Content-Type'] = 'application/json' 
            return (json.dumps({'error': error_message}), 404, response_headers) 

        file_contents = blob.download_as_bytes()
        print(f"File '{gcs_file_path}' downloaded from GCS. Size: {len(file_contents)} bytes")

        # 読み込んだ画像を /tmp ディレクトリに保存 (これは検証のために残します)
        local_temp_file_path = os.path.join("/tmp", f"{received_task_id}.{received_file_extension}")
        with open(local_temp_file_path, "wb") as f:
            f.write(file_contents)
        print(f"File successfully saved to local /tmp: {local_temp_file_path}")

        # --- 読み込んだ画像データをそのままバイナリレスポンスとして返す ---
        # Content-Typeを画像のMIMEタイプに設定
        # received_file_extension を使って Content-Type を推測
        image_mime_type = f"image/{received_file_extension}" # 例: image/png, image/jpeg
        if received_file_extension.lower() == 'jpg': # 拡張子がjpgの場合の一般的なMIMEタイプ
            image_mime_type = 'image/jpeg'
        elif received_file_extension.lower() == 'jpeg':
            image_mime_type = 'image/jpeg'
        elif received_file_extension.lower() == 'png':
            image_mime_type = 'image/png'
        elif received_file_extension.lower() == 'gif':
            image_mime_type = 'image/gif'
        # 他の画像形式にも対応が必要なら追加
        
        response_headers['Content-Type'] = image_mime_type 

        return (file_contents, 200, response_headers)

    except Exception as e:
        # エラー発生時のログ出力とJSONエラーレスポンス
        error_message = f"Unexpected error in Cloud Functions (image return mode): {str(e)}"
        print(f"Error: {error_message}")
        response_headers['Content-Type'] = 'application/json' # エラー時はJSONを返す
        if "File not found in GCS" in str(e) or ("Access denied" in str(e) and "storage.googleapis.com" in str(e)): 
            return (json.dumps({'error': error_message}), 404, response_headers)
        else:
            return (json.dumps({'error': error_message}), 500, response_headers)