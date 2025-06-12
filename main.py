# main.py

import functions_framework
import json
import numpy as np 
from pygltflib import GLTF2, Buffer, BufferView, Accessor, Mesh, Primitive, Node, Scene, Asset
from google.cloud import storage 
import os 
import io 
import time 
import trimesh

# Cloud Storage クライアントの初期化
storage_client = storage.Client()

# Cloud Storage 入力バケット名 (アップロードされた画像が保存される場所)
GCS_INPUT_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "your-gcs-input-bucket-if-not-set")
if GCS_INPUT_BUCKET_NAME == "your-gcs-input-bucket-if-not-set":
    print("WARNING: GCS_INPUT_BUCKET_NAME environment variable is not set. Using placeholder.")

# Cloud Storage 出力バケット名 (生成したモデルを保存する場所)
GCS_OUTPUT_BUCKET_NAME = os.environ.get("GCS_OUTPUT_BUCKET_NAME", "your-gcs-output-bucket-if-not-set")
if GCS_OUTPUT_BUCKET_NAME == "your-gcs-output-bucket-if-not-set":
    print("WARNING: GCS_OUTPUT_BUCKET_NAME environment variable is not set. Using placeholder.")

# 3Dモデル生成のためのヘルパー関数
def create_cylinder(radius, height, transform=None):
    cyl = trimesh.creation.cylinder(radius=radius, height=height)
    if transform is not None:
        cyl.apply_transform(transform)
    return cyl

def create_sphere(radius, center):
    sphere = trimesh.creation.icosphere(radius=radius, subdivisions=3)
    sphere.apply_translation(center)
    return sphere

def create_humanoid():
    parts = []

    # 頭 (sphere)
    head = create_sphere(radius=0.2, center=[0, 0, 1.8])
    parts.append(head)

    # 胴体 (cylinder)
    torso_height = 0.8
    torso = create_cylinder(radius=0.3, height=torso_height, transform=trimesh.transformations.translation_matrix([0, 0, 1.2 - torso_height/2]))
    parts.append(torso)

    # 腕 (cylinder)
    arm_radius = 0.1
    arm_height = 0.6
    left_arm = create_cylinder(radius=arm_radius, height=arm_height, transform=trimesh.transformations.compose_matrix(
        translate=[-0.3 - arm_height/2, 0, 1.4], 
        angles=[0, np.pi/2, 0] 
    ))
    right_arm = create_cylinder(radius=arm_radius, height=arm_height, transform=trimesh.transformations.compose_matrix(
        translate=[0.3 + arm_height/2, 0, 1.4],
        angles=[0, -np.pi/2, 0] 
    ))
    parts.extend([left_arm, right_arm])

    # 脚 (cylinder)
    leg_radius = 0.12
    leg_height = 0.7
    left_leg = create_cylinder(radius=leg_radius, height=leg_height, transform=trimesh.transformations.translation_matrix([-0.15, 0, leg_height/2]))
    right_leg = create_cylinder(radius=leg_radius, height=leg_height, transform=trimesh.transformations.translation_matrix([0.15, 0, leg_height/2]))
    parts.extend([left_leg, right_leg])

    # 全てのパーツを統合して一つのメッシュにする
    humanoid = trimesh.util.concatenate(parts)
    return humanoid


@functions_framework.http
def makemodel(request):
    """
    HTTP リクエストを受け取り、Next.jsから送信されたtaskIdとfileExtensionに基づいて
    Cloud Storageから画像を読み込み、固定のヒューマノイドGLBモデルを生成し、GCSに保存してそのURLを返すCloud Function。
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

    # レスポンスヘッダーの設定 (今回はJSONを返す)
    response_headers = {
        'Access-Control-Allow-Origin': '*',
        'Content-Type': 'application/json' # JSONを返す
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
        return (json.dumps({'error': error_message}), 400, response_headers)

    if not received_task_id:
        error_message = "Missing 'taskId' in request body."
        print(f"Error: {error_message}")
        return (json.dumps({'error': error_message}), 400, response_headers)
    
    if not received_file_extension:
        error_message = "Missing 'fileExtension' in request body."
        print(f"Error: {error_message}")
        return (json.dumps({'error': error_message}), 400, response_headers)

    try:
        # --- GCSから画像を読み込む (画像データは受け取るが、モデル生成には使わない) ---
        gcs_input_file_path = f"uploads/{received_task_id}.{received_file_extension}" 
        print(f"Cloud Functions: Attempting to download input from GCS: gs://{GCS_INPUT_BUCKET_NAME}/{gcs_input_file_path}")

        input_bucket = storage_client.bucket(GCS_INPUT_BUCKET_NAME)
        input_blob = input_bucket.blob(gcs_input_file_path)

        # ファイルが存在しない場合のリトライロジック (タイムラグ吸収用)
        max_retries = 5 
        retry_delay_seconds = 2 

        file_found = False
        for attempt in range(max_retries):
            print(f"Cloud Functions: Checking input file (Attempt {attempt + 1}/{max_retries})")
            if input_blob.exists():
                print(f"Cloud Functions: Input file '{gcs_input_file_path}' found on attempt {attempt + 1}.")
                file_found = True
                break
            else:
                print(f"Cloud Functions: Input file '{gcs_input_file_path}' not found on attempt {attempt + 1}. Retrying in {retry_delay_seconds} seconds...")
                time.sleep(retry_delay_seconds) 

        if not file_found: 
            error_message = f"Input file not found in GCS after {max_retries} attempts: gs://{GCS_INPUT_BUCKET_NAME}/{gcs_input_file_path}. Please check filename or upload status."
            print(f"Error: {error_message}")
            return (json.dumps({'error': error_message}), 404, response_headers)

        file_contents = input_blob.download_as_bytes()
        print(f"DEBUG: Type of input file_contents: {type(file_contents)}, Length: {len(file_contents)} bytes")

        # 読み込んだ画像を /tmp ディレクトリに保存 (これはデバッグのために残します)
        local_temp_file_path = os.path.join("/tmp", f"{received_task_id}.{received_file_extension}")
        with open(local_temp_file_path, "wb") as f:
            f.write(file_contents)
        print(f"Input file successfully saved to local /tmp: {local_temp_file_path}")

        # --- ここからが3Dモデル生成の開始点 ---
        print(f"Cloud Functions: STARTING 3D MODEL GENERATION PROCESS for taskId: {received_task_id}")
        
        humanoid_model = create_humanoid()
        
        glb_buffer = io.BytesIO()
        humanoid_model.export(file_obj=glb_buffer, file_type='glb') 
        glb_data = glb_buffer.getvalue() 
        
        print(f"Cloud Functions: Humanoid model generated. GLB size: {len(glb_data)} bytes.")


        # ★★★ 生成したGLBファイルをGCSに保存し、公開URLを返す ★★★
        # ★ここを修正：出力パスから 'output/' フォルダを削除 ★
        output_blob_name = f"{received_task_id}.glb" # 例: UUID.glb
        output_bucket = storage_client.bucket(GCS_OUTPUT_BUCKET_NAME)
        output_blob = output_bucket.blob(output_blob_name)

        output_blob.upload_from_string(glb_data, content_type='model/gltf-binary')
        print(f"Cloud Functions: GLB model uploaded to GCS: gs://{GCS_OUTPUT_BUCKET_NAME}/{output_blob_name}")

        output_blob.make_public()
        public_url = output_blob.public_url
        print(f"Cloud Functions: Public URL generated: {public_url}")

        # Next.jsにGLBの公開URLをJSONで返す
        response_data = {
            "model_url": public_url,
            "task_id": received_task_id,
            "message": "3D model generated and uploaded successfully!"
        }
        response_headers['Content-Type'] = 'application/json' # JSONを返す
        return (json.dumps(response_data), 200, response_headers)

    except Exception as e:
        error_message = f"3D Model generation or GCS access failed: {str(e)}"
        print(f"Error: {error_message}")
        response_headers['Content-Type'] = 'application/json' 
        if "File not found in GCS" in str(e):
            return (json.dumps({'error': error_message}), 404, response_headers)
        else:
            return (json.dumps({'error': error_message}), 500, response_headers) 