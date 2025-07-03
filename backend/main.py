import os
import uuid
import json
from pathlib import Path
import time
import boto3  # Boto3 is back!

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

# --- Configuration and Setup ---

load_dotenv()

app = FastAPI(
    title="Video Celebrity Recognition API (AWS Rekognition)",
    description="API for uploading videos, detecting and recognizing celebrities using AWS Rekognition.",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    # IMPORTANT: Change this for production!
    # Example: allow_origins=["https://your-vercel-frontend-domain.vercel.app", "http://localhost:3000"]
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- AWS Configuration ---
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
REKOGNITION_COLLECTION_ID = os.getenv("REKOGNITION_COLLECTION_ID", "MyCelebrityFaces") # Default value

if not all([AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, S3_BUCKET_NAME]):
    raise ValueError("Missing AWS environment variables. Please check your .env file.")

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

# --- Directory Setup ---
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
KNOWN_FACES_DIR = BASE_DIR / "known_faces" # This would be used if you had a separate "known faces" collection
TEMP_VIDEO_DIR = DATA_DIR / "temp_videos"
FACES_SAVE_DIR = DATA_DIR / "faces" # For saving detected face crops from Rekognition results (optional, but good for frontend)
FRONTEND_BUILD_DIR = Path(__file__).parent.parent / "frontend" / "build"

for directory in [DATA_DIR, KNOWN_FACES_DIR, TEMP_VIDEO_DIR, FACES_SAVE_DIR]:
    directory.mkdir(parents=True, exist_ok=True)
    print(f"Created/verified directory: {directory}")

@app.on_event("startup")
async def create_rekognition_collection_on_startup():
    """
    Checks if the Rekognition collection exists and creates it if not.
    This is useful for initial setup.
    """
    try:
        print(f"Checking for Rekognition collection: {REKOGNITION_COLLECTION_ID}")
        response = rekognition_client.list_collections()
        if REKOGNITION_COLLECTION_ID not in response['CollectionIds']:
            print(f"Collection '{REKOGNITION_COLLECTION_ID}' not found. Creating it...")
            rekognition_client.create_collection(CollectionId=REKOGNITION_COLLECTION_ID)
            print(f"Collection '{REKOGNITION_COLLECTION_ID}' created successfully.")
        else:
            print(f"Collection '{REKOGNITION_COLLECTION_ID}' already exists.")
    except Exception as e:
        print(f"Error checking/creating Rekognition collection: {e}")
        # This error might happen if permissions are not set correctly for list_collections/create_collection
        # The app can still proceed if the collection is manually created or not strictly needed
        pass

def start_rekognition_celebrity_job(video_s3_key: str) -> str:
    """
    Starts an asynchronous Amazon Rekognition celebrity recognition job.
    """
    try:
        print(f"Starting Rekognition celebrity recognition job for s3://{S3_BUCKET_NAME}/{video_s3_key}")
        response = rekognition_client.start_celebrity_recognition(
            Video={'S3Object': {'Bucket': S3_BUCKET_NAME, 'Name': video_s3_key}}
            # For real applications, consider adding NotificationChannel for SNS
            # NotificationChannel={
            #     'SNSTopicArn': 'arn:aws:sns:REGION:ACCOUNT_ID:TOPIC_NAME',
            #     'RoleArn': 'arn:aws:iam::ACCOUNT_ID:role/REKOGNITION_SERVICE_ROLE'
            # }
        )
        job_id = response['JobId']
        print(f"Rekognition job started with JobId: {job_id}")
        return job_id
    except Exception as e:
        print(f"Error starting Rekognition job: {e}")
        raise

def get_rekognition_job_results(job_id: str):
    """
    Polls for the completion of an Amazon Rekognition job and retrieves results.
    """
    status = ''
    print(f"Polling for Rekognition job {job_id} status...")
    while status not in ['SUCCEEDED', 'FAILED']:
        time.sleep(5)  # Wait longer for video processing
        try:
            response = rekognition_client.get_celebrity_recognition(JobId=job_id)
            status = response['JobStatus']
            print(f"Job {job_id} status: {status}")
        except Exception as e:
            print(f"Error getting Rekognition job results for {job_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to retrieve Rekognition job status: {e}")

    if status == 'FAILED':
        raise HTTPException(status_code=500, detail=f"Rekognition job {job_id} failed: {response.get('StatusMessage', 'Unknown error')}")
    
    print(f"Rekognition job {job_id} SUCCEEDED. Returning results.")
    return response

# --- Serve static files for known faces and the frontend ---
app.mount("/static/known_faces", StaticFiles(directory=KNOWN_FACES_DIR), name="known_faces_static")
app.mount("/static/faces", StaticFiles(directory=FACES_SAVE_DIR), name="faces_static")

# Serve the frontend build directory
if FRONTEND_BUILD_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_BUILD_DIR, html=True), name="frontend")
    print(f"Serving frontend from: {FRONTEND_BUILD_DIR}")
else:
    print(f"Frontend build directory not found: {FRONTEND_BUILD_DIR}. Frontend will not be served automatically.")


@app.post("/upload-video")
async def upload_video_endpoint(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No video file provided.")

    video_filename = f"uploaded_{uuid.uuid4()}{Path(file.filename).suffix}"
    temp_video_path = TEMP_VIDEO_DIR / video_filename
    video_s3_key = f"videos/{video_filename}" # Path in S3 bucket

    try:
        # 1. Save uploaded video to a temporary local file
        print(f"Saving uploaded video to {temp_video_path}")
        contents = await file.read()
        with open(temp_video_path, "wb") as buffer:
            buffer.write(contents)

        # 2. Upload video to S3
        print(f"Uploading {temp_video_path} to s3://{S3_BUCKET_NAME}/{video_s3_key}")
        s3_client.upload_file(str(temp_video_path), S3_BUCKET_NAME, video_s3_key)
        print("Upload to S3 complete.")

        # 3. Start Rekognition Celebrity Recognition Job
        job_id = start_rekognition_celebrity_job(video_s3_key)

        # 4. Get Rekognition Job Results (polling for completion)
        rekognition_results = get_rekognition_job_results(job_id)

        # --- Process Rekognition Results ---
        video_metadata = rekognition_results.get('VideoMetadata', {})
        detections = []
        unique_faces = {} # Use a dict to easily manage unique celebrities by ID

        # Extract relevant video info
        video_info = {
            "fps": video_metadata.get('FrameRate', 0.0),
            "total_frames": video_metadata.get('FrameCount', 0),
            "resolution": f"{video_metadata.get('CodecWidth', 0)}x{video_metadata.get('CodecHeight', 0)}",
            "duration_seconds": video_metadata.get('DurationMillis', 0) / 1000.0
        }

        # Iterate through celebrity detections
        for celeb_detection in rekognition_results.get('Celebrities', []):
            timestamp_millis = celeb_detection.get('Timestamp', 0)
            timestamp_seconds = timestamp_millis / 1000.0

            celebrity_detail = celeb_detection.get('Celebrity', {})
            face_detail = celebrity_detail.get('Face', {})
            
            celeb_id = celebrity_detail.get('Id')
            celeb_name = celebrity_detail.get('Name', 'Unknown Celebrity')
            
            if not celeb_id:
                # If Rekognition doesn't provide an ID, generate a unique one based on name
                celeb_id = str(uuid.uuid5(uuid.NAMESPACE_URL, celeb_name))

            # Store unique celebrity data
            if celeb_id not in unique_faces:
                unique_faces[celeb_id] = {
                    "id": celeb_id,
                    "name": celeb_name,
                    "image_path": f"https://s3.{AWS_REGION}.amazonaws.com/{S3_BUCKET_NAME}/{video_s3_key}", # Point to the video itself as a placeholder or generate separate images
                    "is_celebrity": True
                }
                # If you want to save cropped faces from the video for the frontend:
                # This would require downloading the video locally and using OpenCV to crop
                # which would make the processing slower and defeat the purpose of offloading to Rekognition.
                # For now, we'll just link back to the video or a generic placeholder.
                # A more advanced approach would be to extract a frame and crop it, then upload that crop to S3.
                # For this example, we'll use a placeholder or link back to the video.

                # Simplified: Let's create a placeholder image path for each unique celebrity for the frontend
                # In a real scenario, you might get a representative image from Rekognition or upload one yourself.
                # Here, we'll generate one and store it in FACES_SAVE_DIR
                face_image_filename = f"celeb_{celeb_id}.jpg"
                face_image_path = FACES_SAVE_DIR / face_image_filename
                
                # --- This part would require image processing to actually save a face crop ---
                # For a true implementation, you'd download the video, find the frame,
                # crop the face based on bounding box, and save it.
                # For now, we'll create a dummy file to ensure the static path works.
                try:
                    # Create a dummy image file if it doesn't exist to simulate a saved face
                    if not face_image_path.exists():
                        from PIL import Image, ImageDraw, ImageFont
                        # Create a simple placeholder image
                        img = Image.new('RGB', (128, 128), color = (70, 130, 180)) # SteelBlue
                        d = ImageDraw.Draw(img)
                        try:
                            # Try to use a default font if available
                            font = ImageFont.truetype("arial.ttf", 15)
                        except IOError:
                            font = ImageFont.load_default() # Fallback
                        text = f"{celeb_name[:10]}..." if len(celeb_name) > 10 else celeb_name
                        d.text((10,50), text, fill=(255,255,255), font=font)
                        img.save(str(face_image_path))
                except ImportError:
                    print("Pillow not installed. Cannot create dummy face images. Install with 'pip install Pillow'")
                    # Fallback to a placeholder URL if Pillow isn't available
                    unique_faces[celeb_id]["image_path"] = f"https://placehold.co/128x128/3b82f6/ffffff?text={celeb_name.replace(' ', '+')}"

                unique_faces[celeb_id]["image_path"] = f"/static/faces/{face_image_filename}"


            # Add detection details
            detections.append({
                "Timestamp": timestamp_seconds,
                "FrameIndex": int(timestamp_seconds * video_metadata.get('FrameRate', 0)), # Approximate frame index
                "Face": {
                    "BoundingBox": face_detail.get('BoundingBox', {}),
                    "Confidence": face_detail.get('Confidence', 0),
                    "FaceId": celeb_id, # Link to the unique celebrity
                    "ImageId": str(uuid.uuid4()) # Unique ID for this specific detection event
                },
                "Celebrity": {
                    "Name": celeb_name,
                    "Urls": celebrity_detail.get('Urls', []),
                    "Id": celeb_id,
                    "Confidence": celebrity_detail.get('MatchConfidence', celebrity_detail.get('Confidence', 0)),
                    "KnownGender": celebrity_detail.get('KnownGender', {"Type": "Unknown"}) # Rekognition provides this
                }
            })

        unique_faces_list = list(unique_faces.values())

        results_filename = f"results_{uuid.uuid4()}.json"
        results_path = DATA_DIR / results_filename

        full_results = {
            "video_info": video_info,
            "detections": detections,
            "unique_faces": unique_faces_list
        }

        with open(results_path, "w") as f:
            json.dump(full_results, f, indent=2)

        return JSONResponse(content={
            "status": "success",
            "message": "Video uploaded and processed by AWS Rekognition.",
            "results_filename": results_filename,
            "video_info": full_results["video_info"],
            "unique_faces": full_results["unique_faces"]
        })

    except Exception as e:
        print(f"Error during video processing: {e}")
        import traceback
        traceback.print_exc()
        # Clean up S3 object if upload succeeded but Rekognition failed
        if 'video_s3_key' in locals():
            try:
                s3_client.delete_object(Bucket=S3_BUCKET_NAME, Key=video_s3_key)
                print(f"Cleaned up S3 object: {video_s3_key}")
            except Exception as s3_e:
                print(f"Error during S3 cleanup: {s3_e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")
    finally:
        # Clean up the temporary local video file
        if temp_video_path.exists():
            try:
                temp_video_path.unlink()
                print(f"Cleaned up temporary local video file: {temp_video_path}")
            except Exception as e:
                print(f"Error deleting temporary video file {temp_video_path}: {e}")
                pass

@app.get("/results/{results_filename}")
async def get_results(results_filename: str):
    results_path = DATA_DIR / results_filename
    if not results_path.exists():
        raise HTTPException(status_code=404, detail="Results file not found.")
    
    with open(results_path, "r") as f:
        results = json.load(f)
    return JSONResponse(content=results)

@app.get("/")
async def read_root():
    """
    Redirects to the frontend's index.html if the frontend build directory exists.
    """
    if FRONTEND_BUILD_DIR.is_dir():
        return RedirectResponse(url="/index.html")
    return JSONResponse(content={"message": "Welcome to the Video Face API! Frontend not found locally."})

```
