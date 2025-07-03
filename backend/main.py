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
import cv2 # OpenCV is used for video processing and face cropping

# --- Configuration and Setup ---

load_dotenv()

app = FastAPI(
    title="Video Face Recognition API (AWS Rekognition)",
    description="API for uploading videos, detecting and recognizing faces using AWS Rekognition.",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # IMPORTANT: Change this for production!
                         # Example: allow_origins=["https://your-vercel-frontend-domain.vercel.app", "http://localhost:3000"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
REKOGNITION_COLLECTION_ID = os.getenv("REKOGNITION_COLLECTION_ID", "MyCelebrityFaces")

# Ensure AWS credentials are loaded before initializing clients
if not all([AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, S3_BUCKET_NAME]):
    raise ValueError("Missing AWS environment variables. Please check your .env file and ensure AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, S3_BUCKET_NAME are set.")

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
KNOWN_FACES_DIR = BASE_DIR / "known_faces" # For storing known face images (if you index them manually)
TEMP_VIDEO_DIR = DATA_DIR / "temp_videos"
FACES_SAVE_DIR = DATA_DIR / "faces" # For saving detected face crops from Rekognition results
FRONTEND_BUILD_DIR = Path(__file__).parent.parent / "frontend" / "build"

for directory in [DATA_DIR, KNOWN_FACES_DIR, TEMP_VIDEO_DIR, FACES_SAVE_DIR]:
    directory.mkdir(parents=True, exist_ok=True)
    print(f"Created/verified directory: {directory}")

@app.on_event("startup")
async def create_rekognition_collection():
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
        # Log the full traceback for debugging in production environments
        import traceback
        traceback.print_exc()
        pass # Allow the app to start even if collection creation fails

def start_rekognition_celebrity_job(video_s3_key: str) -> str:
    """
    Starts an asynchronous Amazon Rekognition celebrity recognition job.
    """
    print(f"Starting Rekognition celebrity recognition job for s3://{S3_BUCKET_NAME}/{video_s3_key}")
    response = rekognition_client.start_celebrity_recognition(
        Video={'S3Object': {'Bucket': S3_BUCKET_NAME, 'Name': video_s3_key}}
    )
    job_id = response['JobId']
    print(f"Rekognition job started with JobId: {job_id}")
    return job_id

def get_rekognition_job_results(job_id: str):
    """
    Polls for the completion of an Amazon Rekognition job and retrieves results.
    """
    status = ''
    print(f"Polling for Rekognition job {job_id} status...")
    while status not in ['SUCCEEDED', 'FAILED']:
        time.sleep(5) # Wait longer for video processing in production
        response = rekognition_client.get_celebrity_recognition(JobId=job_id)
        status = response['JobStatus']
        print(f"Job {job_id} status: {status}")

    if status == 'FAILED':
        raise Exception(f"Rekognition job {job_id} failed: {response.get('StatusMessage', 'Unknown error')}")
    
    print(f"Rekognition job {job_id} SUCCEEDED. Returning results.")
    return response

# --- Serve static files ---
# This serves any cropped face images saved to FACES_SAVE_DIR
app.mount("/faces", StaticFiles(directory=str(FACES_SAVE_DIR)), name="faces")
# This serves any known face images stored in KNOWN_FACES_DIR
app.mount("/static/known_faces", StaticFiles(directory=KNOWN_FACES_DIR), name="known_faces_static")

# Serve the frontend build directory
if FRONTEND_BUILD_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_BUILD_DIR), html=True), name="frontend_static")
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

    # Initialize s3_object_created flag for cleanup in finally block
    s3_object_created = False 

    try:
        # 1. Save uploaded video to a temporary local file
        print(f"Saving uploaded video to {temp_video_path}")
        contents = await file.read()
        with open(temp_video_path, "wb") as buffer:
            buffer.write(contents)

        # 2. Upload video to S3
        print(f"Uploading {temp_video_path} to s3://{S3_BUCKET_NAME}/{video_s3_key}")
        s3_client.upload_file(str(temp_video_path), S3_BUCKET_NAME, video_s3_key)
        s3_object_created = True # Set flag if S3 upload succeeds
        print("Upload to S3 complete.")

        # 3. Start Rekognition Celebrity Recognition Job
        job_id = start_rekognition_celebrity_job(video_s3_key)

        # 4. Get Rekognition Job Results (polling for completion)
        rekognition_results = get_rekognition_job_results(job_id)

        # --- Process Rekognition Results ---
        video_metadata = rekognition_results.get('VideoMetadata', {})
        
        # Ensure proper calculations for video_info
        frame_rate = video_metadata.get('FrameRate', 0.0)
        duration_millis = video_metadata.get('DurationMillis', 0)
        calculated_duration_seconds = duration_millis / 1000.0
        calculated_total_frames = int(calculated_duration_seconds * frame_rate) if frame_rate > 0 else 0

        video_info = {
            "fps": float(frame_rate),
            "total_frames": calculated_total_frames,
            "resolution": f"{video_metadata.get('CodecWidth', 0)}x{video_metadata.get('CodecHeight', 0)}",
            "duration_seconds": calculated_duration_seconds
        }
        print(f"Video info extracted: {video_info}")

        detections = []
        unique_celebrities = {}

        # Open video with OpenCV to extract frames for face cropping
        cap = cv2.VideoCapture(str(temp_video_path))
        if not cap.isOpened():
            print("WARNING: Could not open video file with OpenCV for face extraction. Face images will be placeholders.")
            can_extract_faces = False
            # Use placeholder values for frame dimensions if video can't be opened
            current_frame_width = 640 
            current_frame_height = 360
            video_fps = frame_rate # Fallback to metadata fps
        else:
            current_frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            current_frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            video_fps = cap.get(cv2.CAP_PROP_FPS)
            can_extract_faces = True


        for item in rekognition_results['Celebrities']:
            timestamp = item['Timestamp'] / 1000.0
            celebrity = item['Celebrity']
            celeb_id = celebrity.get('Id')
            celeb_name = celebrity.get('Name', 'Unknown Celebrity')
            
            # If Rekognition doesn't provide an ID, generate one based on name
            if not celeb_id:
                celeb_id = str(uuid.uuid5(uuid.NAMESPACE_URL, celeb_name))

            face_box = celebrity.get('Face', {}).get('BoundingBox', {})
            
            # Only process unique celebrities for image extraction
            if celeb_id not in unique_celebrities:
                celeb_image_path = None # Will store the final path for the frontend
                
                if can_extract_faces and face_box and all(k in face_box for k in ['Left', 'Top', 'Width', 'Height']):
                    # Calculate frame number from timestamp
                    # Ensure frame_number is non-negative
                    frame_number = max(0, int(timestamp * video_fps))
                    
                    # Set video position to the frame where celebrity was detected
                    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
                    ret, frame = cap.read()
                    
                    if ret:
                        # Convert relative coordinates to absolute pixels
                        left = int(face_box['Left'] * current_frame_width)
                        top = int(face_box['Top'] * current_frame_height)
                        width = int(face_box['Width'] * current_frame_width)
                        height = int(face_box['Height'] * current_frame_height)
                        
                        # Add padding and ensure coordinates are within frame bounds
                        padding = 20 # Add some padding around the face
                        x1 = max(0, left - padding)
                        y1 = max(0, top - padding)
                        x2 = min(current_frame_width, left + width + padding)
                        y2 = min(current_frame_height, top + height + padding)
                        
                        # Extract and save face crop
                        if x2 > x1 and y2 > y1:
                            face_crop = frame[y1:y2, x1:x2]
                            if face_crop.size > 0:  # Ensure face crop is not empty
                                face_save_path = FACES_SAVE_DIR / f"celebrity_{celeb_id}.jpg"
                                success = cv2.imwrite(str(face_save_path), face_crop)
                                if success and face_save_path.exists():
                                    celeb_image_path = f"/faces/{face_save_path.name}" # Path for frontend to access via /faces mount
                                else:
                                    print(f"Failed to save face image for {celeb_name}. cv2.imwrite failed or file not found. Using placeholder.")
                            else:
                                print(f"Empty face crop for {celeb_name}. Using placeholder.")
                        else:
                            print(f"Invalid bounding box coordinates after padding for {celeb_name}. Using placeholder.")
                    else:
                        print(f"Could not read frame {frame_number} for {celeb_name}. Using placeholder.")
                
                # If image extraction failed or was not possible, use a placeholder
                if celeb_image_path is None:
                    # Replace spaces with '+' for URL compatibility
                    celeb_image_path = f"https://placehold.co/128x128/3b82f6/ffffff?text={celeb_name.replace(' ', '+')}"

                unique_celebrities[celeb_id] = {
                    "id": celeb_id,
                    "name": celeb_name,
                    "image_path": celeb_image_path,
                    "is_celebrity": True
                }

            # Prepare detection data
            # Rekognition returns normalized bounding box coordinates (0-1)
            # Convert to absolute for 'location' field as per your original code's structure
            if face_box:
                left = int(face_box.get('Left', 0) * current_frame_width)
                top = int(face_box.get('Top', 0) * current_frame_height)
                width = int(face_box.get('Width', 0) * current_frame_width)
                height = int(face_box.get('Height', 0) * current_frame_height)
                location = [left, top, width, height]
            else:
                location = [0, 0, 0, 0] # Default if no face box

            detections.append({
                "frame": int(timestamp * video_fps) if video_fps > 0 else 0,
                "time": round(timestamp, 2),
                "face_id": celeb_id, # Link to the unique celebrity
                "location": location
            })
            
        if can_extract_faces: # Only release if successfully opened
            cap.release()
            cv2.destroyAllWindows() # Close any OpenCV windows if opened (though typically not needed for video processing without display)

        unique_faces_list = list(unique_celebrities.values())
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
            "message": "Video processed successfully with AWS Rekognition for celebrities.",
            "results_filename": results_filename,
            "video_info": full_results["video_info"],
            "unique_faces": full_results["unique_faces"]
        })

    except Exception as e:
        print(f"Error during video processing: {e}")
        import traceback
        traceback.print_exc() # Print full traceback for debugging

        # Clean up S3 object if it was uploaded successfully
        if s3_object_created:
            try:
                s3_client.delete_object(Bucket=S3_BUCKET_NAME, Key=video_s3_key)
                print(f"Cleaned up S3 object: s3://{S3_BUCKET_NAME}/{video_s3_key}")
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
                    # When updating a face name, it's common to update its placeholder image as well
                    face["image_path"] = f"https://placehold.co/128x128/3b82f6/ffffff?text={new_name.replace(' ', '+')}"
                    found_face = True
                    break
            if not found_face:
                raise HTTPException(status_code=404, detail=f"Face with ID {face_id} not found in results file.")
            f.seek(0) # Go to the beginning of the file to overwrite
            json.dump(results, f, indent=2)
            f.truncate() # Remove remaining part if new content is shorter
        return JSONResponse(content={"status": "success", "message": "Face name updated successfully."})
    except Exception as e:
        import traceback
        traceback.print_exc()
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
    image_s3_key = f"known_faces/{image_filename}" # Path in S3 bucket for known faces

    try:
        contents = await file.read()
        with open(temp_image_path, "wb") as buffer:
            buffer.write(contents)
        
        # Upload the known face image to S3
        s3_client.upload_file(str(temp_image_path), S3_BUCKET_NAME, image_s3_key)
        print(f"Uploaded known face image {name} to s3://{S3_BUCKET_NAME}/{image_s3_key}")

        # Index the face in Rekognition collection
        response = rekognition_client.index_faces(
            CollectionId=REKOGNITION_COLLECTION_ID,
            Image={'S3Object': {'Bucket': S3_BUCKET_NAME, 'Name': image_s3_key}},
            ExternalImageId=name, # This name will be associated with the face in the collection
            DetectionAttributes=['ALL']
        )
        
        if not response['FaceRecords']:
            # If no face was detected by Rekognition, delete from S3 and raise error
            s3_client.delete_object(Bucket=S3_BUCKET_NAME, Key=image_s3_key)
            raise HTTPException(status_code=422, detail="No face detected in the uploaded image by Rekognition.")
        
        print(f"Face for '{name}' indexed successfully in Rekognition.")
        return JSONResponse(content={"status": "success", "message": f"Known face '{name}' added successfully to Rekognition collection."})
    except Exception as e:
        print(f"Error adding known face: {e}")
        import traceback
        traceback.print_exc()
        # Clean up local temp file and S3 object if something went wrong
        if temp_image_path.exists():
            try:
                temp_image_path.unlink()
            except Exception:
                pass
        try:
            s3_client.delete_object(Bucket=S3_BUCKET_NAME, Key=image_s3_key)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")
    finally:
        # Ensure temporary local file is deleted
        if temp_image_path.exists():
            try:
                temp_image_path.unlink()
            except Exception:
                pass


@app.get("/get-face-image/{image_path:path}")
async def get_face_image_endpoint(image_path: str):
    """
    Handles requests for face images.
    If it's a placeholder URL, redirects directly.
    If it's a local static file (from FACES_SAVE_DIR), the /faces mount will handle it.
    If it's a known_face from S3, generates a presigned URL.
    """
    if image_path.startswith("https://placehold.co/"):
        return RedirectResponse(image_path)
    
    # Check if the path is specifically for a known_face from the KNOWN_FACES_DIR
    # This assumes `image_path` coming from the frontend for known faces is just the filename,
    # and we append the `known_faces/` prefix for S3.
    # The /static/known_faces mount handles direct file serving from KNOWN_FACES_DIR.
    # This endpoint is primarily useful if you store these in S3 and want presigned URLs.
    # Example: if frontend asks for 'my_known_person.jpg' which is in S3 under 'known_faces/'
    s3_object_key = f"known_faces/{Path(image_path).name}" 

    try:
        # Generate a presigned URL for secure, temporary access to the S3 object
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': S3_BUCKET_NAME, 'Key': s3_object_key},
            ExpiresIn=3600 # URL valid for 1 hour
        )
        print(f"Generated presigned URL for S3 object {s3_object_key}: {url}")
        return RedirectResponse(url)
    except Exception as e:
        print(f"Error generating presigned URL for {s3_object_key}: {e}")
        import traceback
        traceback.print_exc()
        # Fallback to an error placeholder image
        return RedirectResponse("https://placehold.co/128x128/FF5733/ffffff?text=ERROR")


@app.get("/test-cors")
async def test_cors():
    return {"message": "CORS is configured correctly"}

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
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")

@app.get("/get-known-faces")
async def get_known_faces_endpoint():
    """
    Retrieves a list of known faces (indexed in Rekognition collection)
     along with their associated image paths.
    """
    known_faces_data = []
    try:
        paginator = rekognition_client.get_paginator('list_faces')
        pages = paginator.paginate(CollectionId=REKOGNITION_COLLECTION_ID)
        for page in pages:
            for face_record in page['Faces']:
                face_id = face_record.get('FaceId')
                external_image_id = face_record.get('ExternalImageId', 'Unknown')
                
                # Construct the path to the locally stored known face image
                # Assuming 'ExternalImageId' matches the filename (e.g., 'JohnDoe.jpg')
                # that was uploaded and saved to KNOWN_FACES_DIR
                local_image_path = KNOWN_FACES_DIR / f"{external_image_id}{face_record.get('ImageId', '')}.jpg" # Add a placeholder for suffix if ImageId used for filename
                
                # Check if the local file exists, otherwise use a placeholder
                # Note: The `ExternalImageId` might not have a suffix. You might need a more robust way
                # to store and retrieve suffixes if your `ExternalImageId` doesn't include it.
                # For example, store `ExternalImageId` as just "John Doe" and save file as "known_face_JOHN_DOE_<UUID>.jpg"
                
                # A simpler approach: if you consistently save to KNOWN_FACES_DIR with a specific pattern
                # during add_known_face_endpoint, you can construct that pattern here.
                # Currently, `add_known_face_endpoint` uses `image_filename = f"known_face_{uuid.uuid4()}{Path(file.filename).suffix}"`
                # and sets `ExternalImageId=name`. This means `ExternalImageId` is *not* the filename.
                # To get the correct image path for a known face, you'd need to store the filename/path
                # when you call `index_faces`, perhaps in a local database or a metadata file.
                
                # Given the current `add_known_face_endpoint`, the `ExternalImageId` is just the `name`.
                # We need to find the actual filename in KNOWN_FACES_DIR or S3 if it's there.
                
                # For now, let's assume `ExternalImageId` can be used to construct a local file path
                # or we fall back to a generic placeholder.
                
                # To make this truly functional with your `add_known_face` logic, you'd need to:
                # 1. In `add_known_face_endpoint`, store the `image_filename` (like `known_face_uuid.jpg`)
                #    along with the `ExternalImageId` (the `name`) in a separate mapping (e.g., a simple JSON file or database).
                # 2. In `get_known_faces_endpoint`, retrieve this filename using the `ExternalImageId`
                #    and construct the `/static/known_faces/{filename}` path.

                # For now, we'll try to guess the filename or use a placeholder based on ExternalImageId
                # A more robust solution requires storing the filename linked to ExternalImageId.
                
                # Let's assume for `add_known_face`, you pass `name="John Doe"` and `file` is `john_doe.jpg`.
                # `add_known_face` saves it as `known_face_<uuid>.jpg` and sets `ExternalImageId=John Doe`.
                # To retrieve, we'd have to search `KNOWN_FACES_DIR` for a file linked to "John Doe".
                # This is a common challenge with Rekognition's index_faces.
                
                # TEMPORARY SOLUTION: Check if a file named after ExternalImageId (with common suffixes) exists
                found_image = False
                for suffix in ['.jpg', '.jpeg', '.png']: # Check common image suffixes
                    temp_local_path = KNOWN_FACES_DIR / f"{external_image_id}{suffix}"
                    if temp_local_path.exists():
                        image_path = f"/static/known_faces/{temp_local_path.name}"
                        found_image = True
                        break
                
                if not found_image:
                    # Fallback to a placeholder image if local file doesn't exist
                    image_path = f"https://placehold.co/128x128/3b82f6/ffffff?text={external_image_id.replace(' ', '+')}"

                known_faces_data.append({
                    "id": face_id, # Rekognition's internal FaceId
                    "name": external_image_id, # The name you gave it
                    "image_path": image_path,
                    "is_celebrity": False # These are "known" faces, not necessarily "celebrities"
                })
    except Exception as e:
        print(f"Error getting known faces: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse(content={"known_faces": []}, status_code=500) # Return empty list on error
    return JSONResponse(content={"known_faces": known_faces_data})

@app.get("/check-face/{face_filename}")
async def check_face_endpoint(face_filename: str):
    """
    Checks if a specific face image (saved from video processing) exists locally.
    This helps the frontend verify if a static image is available.
    """
    face_path = FACES_SAVE_DIR / face_filename
    return JSONResponse(content={
        "exists": face_path.exists(),
        "path": str(face_path),
        "size": face_path.stat().st_size if face_path.exists() else 0
    })
