import os
import uuid
import json
from pathlib import Path
import time

# import boto3  # 🔒 AWS functionality disabled for now
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
import cv2

# --- Configuration and Setup ---

load_dotenv()

app = FastAPI(
    title="Video Face Recognition API (AWS Rekognition)",
    description="API for uploading videos, detecting and recognizing faces using AWS Rekognition.",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
# AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
# AWS_REGION = os.getenv("AWS_REGION")
# S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
# REKOGNITION_COLLECTION_ID = "MyCelebrityFaces"

# s3_client = boto3.client(
#     's3',
#     aws_access_key_id=AWS_ACCESS_KEY_ID,
#     aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
#     region_name=AWS_REGION
# )
# rekognition_client = boto3.client(
#     'rekognition',
#     aws_access_key_id=AWS_ACCESS_KEY_ID,
#     aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
#     region_name=AWS_REGION
# )

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
KNOWN_FACES_DIR = BASE_DIR / "known_faces"
TEMP_VIDEO_DIR = DATA_DIR / "temp_videos"
FACES_SAVE_DIR = DATA_DIR / "faces"
FRONTEND_BUILD_DIR = Path(__file__).parent.parent / "frontend" / "build"

for directory in [DATA_DIR, KNOWN_FACES_DIR, TEMP_VIDEO_DIR, FACES_SAVE_DIR]:
    directory.mkdir(parents=True, exist_ok=True)
    print(f"Created/verified directory: {directory}")

# @app.on_event("startup")
# async def create_rekognition_collection():
#     try:
#         response = rekognition_client.list_collections()
#         if REKOGNITION_COLLECTION_ID not in response['CollectionIds']:
#             rekognition_client.create_collection(CollectionId=REKOGNITION_COLLECTION_ID)
#     except Exception:
#         pass

# def start_rekognition_celebrity_job(video_s3_key: str) -> str:
#     response = rekognition_client.start_celebrity_recognition(
#         Video={'S3Object': {'Bucket': S3_BUCKET_NAME, 'Name': video_s3_key}}
#     )
#     return response['JobId']

# def get_rekognition_job_results(job_id: str):
#     status = ''
#     while status not in ['SUCCEEDED', 'FAILED']:
#         time.sleep(2)
#         response = rekognition_client.get_celebrity_recognition(JobId=job_id)
#         status = response['JobStatus']
#     if status == 'FAILED':
#         raise Exception(f"Rekognition job {job_id} failed: {response.get('StatusMessage', 'Unknown error')}")
#     return response

@app.post("/upload-video")
async def upload_video_endpoint(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No video file provided.")

    video_filename = f"uploaded_{uuid.uuid4()}{Path(file.filename).suffix}"
    temp_video_path = TEMP_VIDEO_DIR / video_filename
    # video_s3_key = f"videos/{video_filename}"

    try:
        contents = await file.read()
        with open(temp_video_path, "wb") as buffer:
            buffer.write(contents)

        # s3_client.upload_file(str(temp_video_path), S3_BUCKET_NAME, video_s3_key)

        # job_id = start_rekognition_celebrity_job(video_s3_key)
        # rekognition_results = get_rekognition_job_results(job_id)

        # --- Simulated dummy data when Rekognition is disabled ---
        frame_rate = 24
        calculated_duration_seconds = 10
        calculated_total_frames = 240
        current_frame_width = 640
        current_frame_height = 360

        detections = []
        unique_celebrities = {}

        # Dummy single entry
        unique_faces_list = [{
            "id": "simulated_id",
            "name": "Demo Celebrity",
            "image_path": "https://placehold.co/128x128/3b82f6/ffffff?text=Demo",
            "is_celebrity": True
        }]

        results_filename = f"results_{uuid.uuid4()}.json"
        results_path = DATA_DIR / results_filename

        full_results = {
            "video_info": {
                "fps": float(frame_rate),
                "total_frames": calculated_total_frames,
                "resolution": f"{current_frame_width}x{current_frame_height}",
                "duration_seconds": calculated_duration_seconds
            },
            "detections": detections,
            "unique_faces": unique_faces_list
        }

        with open(results_path, "w") as f:
            json.dump(full_results, f, indent=2)

        return JSONResponse(content={
            "status": "success",
            "message": "Video uploaded successfully (AWS Rekognition disabled).",
            "results_filename": results_filename,
            "video_info": full_results["video_info"],
            "unique_faces": full_results["unique_faces"]
        })

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")
    finally:
        if temp_video_path.exists():
            try:
                temp_video_path.unlink()
            except Exception:
                pass
