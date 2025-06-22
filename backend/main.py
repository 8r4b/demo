import os
import uuid
import json
from pathlib import Path
import time

import boto3
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

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
REKOGNITION_COLLECTION_ID = "MyCelebrityFaces"

s3_client = boto3.client(
    's3',
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION
)
rekognition_client = boto3.client(
    'rekognition',
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION
)

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
KNOWN_FACES_DIR = BASE_DIR / "known_faces"
TEMP_VIDEO_DIR = DATA_DIR / "temp_videos"
FACES_SAVE_DIR = DATA_DIR / "faces"
FRONTEND_BUILD_DIR = Path(__file__).parent.parent / "frontend" / "build"

for directory in [DATA_DIR, KNOWN_FACES_DIR, TEMP_VIDEO_DIR, FACES_SAVE_DIR]:
    directory.mkdir(parents=True, exist_ok=True)
    print(f"Created/verified directory: {directory}")

@app.on_event("startup")
async def create_rekognition_collection():
    try:
        response = rekognition_client.list_collections()
        if REKOGNITION_COLLECTION_ID not in response['CollectionIds']:
            rekognition_client.create_collection(CollectionId=REKOGNITION_COLLECTION_ID)
    except Exception:
        pass

def start_rekognition_celebrity_job(video_s3_key: str) -> str:
    response = rekognition_client.start_celebrity_recognition(
        Video={'S3Object': {'Bucket': S3_BUCKET_NAME, 'Name': video_s3_key}}
    )
    return response['JobId']

def get_rekognition_job_results(job_id: str):
    status = ''
    while status not in ['SUCCEEDED', 'FAILED']:
        time.sleep(2)
        response = rekognition_client.get_celebrity_recognition(JobId=job_id)
        status = response['JobStatus']
    if status == 'FAILED':
        raise Exception(f"Rekognition job {job_id} failed: {response.get('StatusMessage', 'Unknown error')}")
    return response

@app.post("/upload-video")
async def upload_video_endpoint(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No video file provided.")

    video_filename = f"uploaded_{uuid.uuid4()}{Path(file.filename).suffix}"
    temp_video_path = TEMP_VIDEO_DIR / video_filename
    video_s3_key = f"videos/{video_filename}"

    try:
        contents = await file.read()
        with open(temp_video_path, "wb") as buffer:
            buffer.write(contents)

        s3_client.upload_file(str(temp_video_path), S3_BUCKET_NAME, video_s3_key)

        job_id = start_rekognition_celebrity_job(video_s3_key)
        rekognition_results = get_rekognition_job_results(job_id)

        video_metadata = rekognition_results.get('VideoMetadata', {})
        frame_rate = video_metadata.get('FrameRate', 0)
        duration_millis = video_metadata.get('DurationMillis', 0)
        calculated_duration_seconds = duration_millis / 1000.0 if duration_millis > 0 else 0.0
        calculated_total_frames = int(calculated_duration_seconds * frame_rate) if frame_rate > 0 else 0

        detections = []
        unique_celebrities = {}

        cap = cv2.VideoCapture(str(temp_video_path))
        if not cap.isOpened():
            raise HTTPException(status_code=500, detail="Could not open video file for face extraction.")

        current_frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        current_frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        video_fps = cap.get(cv2.CAP_PROP_FPS)

        for item in rekognition_results['Celebrities']:
            timestamp = item['Timestamp'] / 1000.0
            celebrity = item['Celebrity']
            celeb_id = celebrity['Id']
            celeb_name = celebrity['Name']
            
            # Get face bounding box from Rekognition
            face_box = celebrity.get('Face', {}).get('BoundingBox', {})
            
            # Extract face image if we haven't saved one for this celebrity yet
            celeb_image_path = f"faces/celebrity_{celeb_id}.jpg"
            
            if celeb_id not in unique_celebrities:
                # Calculate frame number from timestamp
                frame_number = int(timestamp * video_fps)
                
                # Set video position to the frame where celebrity was detected
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
                ret, frame = cap.read()
                
                if ret and face_box and all(k in face_box for k in ['Left', 'Top', 'Width', 'Height']):
                    # Convert relative coordinates to absolute pixels
                    left = int(face_box['Left'] * current_frame_width)
                    top = int(face_box['Top'] * current_frame_height)
                    width = int(face_box['Width'] * current_frame_width)
                    height = int(face_box['Height'] * current_frame_height)
                    
                    # Add padding and ensure coordinates are within frame bounds
                    padding = 20
                    x1 = max(0, left - padding)
                    y1 = max(0, top - padding)
                    x2 = min(current_frame_width, left + width + padding)
                    y2 = min(current_frame_height, top + height + padding)
                    
                    # Extract and save face
                    if x2 > x1 and y2 > y1:
                        face_crop = frame[y1:y2, x1:x2]
                        if face_crop.size > 0:  # Ensure face crop is not empty
                            face_save_path = FACES_SAVE_DIR / f"celebrity_{celeb_id}.jpg"
                            success = cv2.imwrite(str(face_save_path), face_crop)
                            if success and face_save_path.exists():
                                celeb_image_path = f"faces/celebrity_{celeb_id}.jpg"
                            else:
                                celeb_image_path = f"https://placehold.co/128x128/3b82f6/ffffff?text={celeb_name.replace(' ', '+')}"
                        else:
                            celeb_image_path = f"https://placehold.co/128x128/3b82f6/ffffff?text={celeb_name.replace(' ', '+')}"
                    else:
                        celeb_image_path = f"https://placehold.co/128x128/3b82f6/ffffff?text={celeb_name.replace(' ', '+')}"
                else:
                    celeb_image_path = f"https://placehold.co/128x128/3b82f6/ffffff?text={celeb_name.replace(' ', '+')}"

                unique_celebrities[celeb_id] = {
                    "id": celeb_id,
                    "name": celeb_name,
                    "image_path": celeb_image_path,
                    "is_celebrity": True
                }

            # Convert bounding box to absolute coordinates for detection
            if face_box:
                left = int(face_box.get('Left', 0) * current_frame_width)
                top = int(face_box.get('Top', 0) * current_frame_height)
                width = int(face_box.get('Width', 0) * current_frame_width)
                height = int(face_box.get('Height', 0) * current_frame_height)
                location = [left, top, width, height]
            else:
                location = [0, 0, 0, 0]

            detections.append({
                "frame": int(timestamp * video_fps) if video_fps > 0 else 0,
                "time": round(timestamp, 2),
                "face_id": celeb_id,
                "location": location
            })

        cap.release()
        cv2.destroyAllWindows()

        unique_faces_list = list(unique_celebrities.values())
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
            "message": "Video processed successfully with AWS Rekognition for celebrities.",
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

@app.post("/update-face")
async def update_face_endpoint(
    face_id: str = Form(...),
    new_name: str = Form(...),
    results_filename: str = Form(...)
):
    json_path = DATA_DIR / results_filename
    if not json_path.exists():
        raise HTTPException(status_code=404, detail="Results file not found.")

    try:
        with open(json_path, "r+") as f:
            results = json.load(f)
            found_face = False
            for face in results["unique_faces"]:
                if face["id"] == face_id:
                    face["name"] = new_name
                    face["image_path"] = f"https://placehold.co/128x128/3b82f6/ffffff?text={new_name.replace(' ', '+')}"
                    found_face = True
                    break
            if not found_face:
                raise HTTPException(status_code=404, detail=f"Face with ID {face_id} not found in results file.")
            f.seek(0)
            json.dump(results, f, indent=2)
            f.truncate()
        return JSONResponse(content={"status": "success", "message": "Face name updated successfully."})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")

@app.post("/add-known-face")
async def add_known_face_endpoint(
    name: str = Form(...),
    file: UploadFile = File(...)
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No image file provided.")

    image_filename = f"known_face_{uuid.uuid4()}{Path(file.filename).suffix}"
    temp_image_path = KNOWN_FACES_DIR / image_filename
    image_s3_key = f"known_faces/{image_filename}"

    try:
        contents = await file.read()
        with open(temp_image_path, "wb") as buffer:
            buffer.write(contents)
        s3_client.upload_file(str(temp_image_path), S3_BUCKET_NAME, image_s3_key)
        response = rekognition_client.index_faces(
            CollectionId=REKOGNITION_COLLECTION_ID,
            Image={'S3Object': {'Bucket': S3_BUCKET_NAME, 'Name': image_s3_key}},
            ExternalImageId=name,
            DetectionAttributes=['ALL']
        )
        if not response['FaceRecords']:
            raise HTTPException(status_code=422, detail="No face detected in the uploaded image.")
        return JSONResponse(content={"status": "success", "message": f"Known face '{name}' added successfully to Rekognition collection."})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")
    finally:
        if temp_image_path.exists():
            temp_image_path.unlink()

@app.get("/get-face-image/{image_path:path}")
async def get_face_image_endpoint(image_path: str):
    if image_path.startswith("https://placehold.co/"):
        return RedirectResponse(image_path)
    s3_object_key = f"known_faces/{Path(image_path).name}"
    try:
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': S3_BUCKET_NAME, 'Key': s3_object_key},
            ExpiresIn=3600
        )
        return RedirectResponse(url)
    except Exception:
        return RedirectResponse("https://placehold.co/128x128/FF5733/ffffff?text=ERROR")

@app.get("/get-results/{results_filename}")
async def get_results_endpoint(results_filename: str):
    json_path = DATA_DIR / results_filename
    if not json_path.exists():
        raise HTTPException(status_code=404, detail="Results file not found.")
    try:
        with open(json_path, "r") as f:
            results_data = json.load(f)
        return JSONResponse(content=results_data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")

@app.get("/get-known-faces")
async def get_known_faces_endpoint():
    known_face_names = []
    try:
        paginator = rekognition_client.get_paginator('list_faces')
        pages = paginator.paginate(CollectionId=REKOGNITION_COLLECTION_ID)
        for page in pages:
            for face in page['Faces']:
                if 'ExternalImageId' in face:
                    known_face_names.append(face['ExternalImageId'])
    except Exception:
        return JSONResponse(content={"known_faces": []})
    return JSONResponse(content={"known_faces": known_face_names})

app.mount("/faces", StaticFiles(directory=str(FACES_SAVE_DIR)), name="faces")

@app.get("/check-face/{face_filename}")
async def check_face_endpoint(face_filename: str):
    face_path = FACES_SAVE_DIR / face_filename
    return JSONResponse(content={
        "exists": face_path.exists(),
        "path": str(face_path),
        "size": face_path.stat().st_size if face_path.exists() else 0
    })
if FRONTEND_BUILD_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_BUILD_DIR), html=True), name="frontend_static")

