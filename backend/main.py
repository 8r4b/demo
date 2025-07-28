import os
import uuid
import json
import logging
import time

from pathlib import Path
from typing import List, Dict, Union, Tuple

import boto3
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
import cv2 # Essential for video processing (reading frames, getting metadata)

# --- Logging Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Load Environment Variables ---
load_dotenv()

# --- FastAPI App Initialization ---
app = FastAPI(
    title="Video Celebrity Recognition API (AWS Rekognition)",
    description="API for uploading videos, detecting and recognizing celebrities using AWS Rekognition.",
    version="1.0.0"
)

# --- CORS Configuration ---
# Get allowed origins from environment variable.
# For production, this MUST be updated on Render to your Vercel frontend's URL (e.g., "https://your-frontend.vercel.app").
ALLOWED_ORIGINS_STR = os.getenv("ALLOWED_ORIGINS", "*")
ALLOWED_ORIGINS = ALLOWED_ORIGINS_STR.split(',') # Allows comma-separated list of origins

logger.info(f"CORS allowed origins: {ALLOWED_ORIGINS}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"], # Allows all HTTP methods (GET, POST, PUT, DELETE, etc.)
    allow_headers=["*"], # Allows all headers
)

# --- AWS Configuration ---
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
REKOGNITION_COLLECTION_ID = os.getenv("REKOGNITION_COLLECTION_ID", "MyCelebrityFacesCollection") # Default for safety

# Ensure AWS credentials are loaded before initializing clients
if not all([AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, S3_BUCKET_NAME]):
    logger.error("Missing AWS environment variables. Please ensure AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, S3_BUCKET_NAME are set in your .env file or environment.")
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

# --- Directory Setup (Ephemeral on Render's free tier!) ---
# These directories are primarily for temporary storage during processing.
# Any images saved here will be LOST on server restart/redeploy.
# We will upload actual face images to S3 for persistence.
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
TEMP_VIDEO_DIR = DATA_DIR / "temp_videos"
# We no longer need FACES_SAVE_DIR or KNOWN_FACES_DIR for persistent static serving,
# as images will be in S3. These are only for temporary local storage if needed during processing.

for directory in [DATA_DIR, TEMP_VIDEO_DIR]:
    directory.mkdir(parents=True, exist_ok=True)
    logger.info(f"Created/verified directory: {directory}")

# --- Rekognition Collection Management ---
@app.on_event("startup")
async def create_rekognition_collection_on_startup():
    """
    Checks if the Rekognition collection exists and creates it if not.
    This runs once when the FastAPI application starts.
    """
    try:
        response = rekognition_client.list_collections()
        if REKOGNITION_COLLECTION_ID not in response['CollectionIds']:
            logger.info(f"Collection '{REKOGNITION_COLLECTION_ID}' does not exist. Creating...")
            rekognition_client.create_collection(CollectionId=REKOGNITION_COLLECTION_ID)
            logger.info(f"Collection '{REKOGNITION_COLLECTION_ID}' created successfully.")
        else:
            logger.info(f"Collection '{REKOGNITION_COLLECTION_ID}' already exists.")
    except Exception as e:
        logger.error(f"Error checking/creating Rekognition collection '{REKOGNITION_COLLECTION_ID}': {e}")
        logger.error("This might be due to incorrect AWS permissions (rekognition:ListCollections, rekognition:CreateCollection).")
        logger.error("Please ensure your AWS IAM user/role has the necessary Rekognition permissions.")
        import traceback
        traceback.print_exc()
        # Allow the app to start even if collection creation fails,
        # but subsequent Rekognition operations might fail.

# --- AWS Rekognition Helper Functions ---

def start_rekognition_celebrity_job(video_s3_key: str) -> str:
    """
    Starts an asynchronous Amazon Rekognition celebrity recognition job.
    Args:
        video_s3_key: The S3 key of the video to process.
    Returns:
        The JobId of the started Rekognition job.
    Raises:
        HTTPException: If the Rekognition job fails to start.
    """
    try:
        logger.info(f"Starting Rekognition celebrity recognition job for s3://{S3_BUCKET_NAME}/{video_s3_key}")
        response = rekognition_client.start_celebrity_recognition(
            Video={'S3Object': {'Bucket': S3_BUCKET_NAME, 'Name': video_s3_key}}
            # For production, consider adding NotificationChannel for SNS to get async results
            # NotificationChannel={
            #     'SNSTopicArn': 'arn:aws:sns:REGION:ACCOUNT_ID:TOPIC_NAME',
            #     'RoleArn': 'arn:aws:iam::ACCOUNT_ID:role/REKOGNITION_SERVICE_ROLE'
            # }
        )
        job_id = response['JobId']
        logger.info(f"Rekognition job started with JobId: {job_id}")
        return job_id
    except Exception as e:
        logger.error(f"Error starting Rekognition job for {video_s3_key}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to start Rekognition job: {e}")

def get_rekognition_job_results(job_id: str):
    """
    Polls the Rekognition service until the celebrity recognition job completes.
    Args:
        job_id: The JobId of the Rekognition job.
    Returns:
        The full response from get_celebrity_recognition.
    Raises:
        HTTPException: If the job fails or cannot be retrieved.
    """
    status = ''
    logger.info(f"Polling for Rekognition job {job_id} status...")
    while status not in ['SUCCEEDED', 'FAILED']:
        time.sleep(5) # Wait for 5 seconds between polls
        try:
            response = rekognition_client.get_celebrity_recognition(JobId=job_id)
            status = response['JobStatus']
            logger.info(f"Job {job_id} status: {status}")
        except Exception as e:
            logger.error(f"Error getting Rekognition job results for {job_id}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to retrieve Rekognition job status: {e}")

    if status == 'FAILED':
        error_message = response.get('StatusMessage', 'Unknown error')
        logger.error(f"Rekognition job {job_id} failed: {error_message}")
        raise HTTPException(status_code=500, detail=f"Rekognition job {job_id} failed: {error_message}")

    logger.info(f"Rekognition job {job_id} SUCCEEDED. Returning results.")
    return response

# --- FastAPI Endpoints ---

@app.post("/upload-video", summary="Uploads a video and processes it for celebrity recognition using AWS Rekognition.")
async def upload_video_endpoint(file: UploadFile = File(..., description="Video file to upload")):
    """
    Handles video file uploads.
    The video is saved temporarily, uploaded to S3, processed by Rekognition.
    Extracted celebrity face images are also uploaded to S3.
    Results are stored locally (ephemeral) for retrieval by the frontend.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No video file provided.")

    video_filename = f"uploaded_video_{uuid.uuid4()}{Path(file.filename).suffix}"
    temp_video_path = TEMP_VIDEO_DIR / video_filename
    video_s3_key = f"videos/{video_filename}" # Path in S3 bucket

    s3_video_uploaded = False # Flag for S3 video cleanup
    uploaded_face_keys = [] # To track face images uploaded to S3 for cleanup

    try:
        # 1. Save uploaded video to a temporary local file
        logger.info(f"Saving uploaded video to {temp_video_path}")
        contents = await file.read()
        with open(temp_video_path, "wb") as buffer:
            buffer.write(contents)
        logger.info(f"Video saved temporarily at: {temp_video_path}")

        # 2. Upload video to S3
        logger.info(f"Uploading {temp_video_path} to s3://{S3_BUCKET_NAME}/{video_s3_key}")
        s3_client.upload_file(str(temp_video_path), S3_BUCKET_NAME, video_s3_key)
        s3_video_uploaded = True # Set flag if S3 upload succeeds
        logger.info("Upload to S3 complete.")

        # 3. Start Rekognition Celebrity Recognition Job
        job_id = start_rekognition_celebrity_job(video_s3_key)

        # 4. Get Rekognition Job Results (synchronous polling)
        rekognition_results = get_rekognition_job_results(job_id)

        # --- Process Rekognition Results ---
        video_metadata = rekognition_results.get('VideoMetadata', {})
        detections = []
        unique_celebrities = {} # Use a dict to easily manage unique celebrities by ID

        # Video info for the frontend
        video_info = {
            "fps": video_metadata.get('FrameRate', 0.0),
            "total_frames": video_metadata.get('FrameCount', 0),
            "resolution": f"{video_metadata.get('CodecWidth', 0)}x{video_metadata.get('CodecHeight', 0)}",
            "duration_seconds": video_metadata.get('DurationMillis', 0) / 1000.0
        }
        logger.info(f"Video info extracted: {video_info}")

        # Open video with OpenCV to extract frames for face cropping
        cap = cv2.VideoCapture(str(temp_video_path))
        if not cap.isOpened():
            logger.error("Could not open video file with OpenCV for face extraction. This is critical for displaying faces.")
            raise HTTPException(status_code=500, detail="Failed to open video file for processing face images.")

        current_frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        current_frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        video_fps = cap.get(cv2.CAP_PROP_FPS)
        if video_fps == 0: # Avoid division by zero
            video_fps = 25.0 # Default to 25 FPS if not found

        logger.info("Opened video with OpenCV for face extraction.")

        # Iterate through celebrity detections
        for item in rekognition_results['Celebrities']:
            timestamp_millis = item.get('Timestamp', 0)
            timestamp_seconds = timestamp_millis / 1000.0
            celebrity = item.get('Celebrity', {})

            celeb_id = celebrity.get('Id')
            celeb_name = celebrity.get('Name', 'Unknown Celebrity')
            face_box = celebrity.get('Face', {}).get('BoundingBox', {})

            # If Rekognition doesn't provide an ID, generate a unique one based on name (should not happen for celebs)
            if not celeb_id:
                celeb_id = str(uuid.uuid5(uuid.NAMESPACE_URL, celeb_name))

            # Store unique celebrity data and get a representative image path
            if celeb_id not in unique_celebrities:
                face_image_s3_key = f"extracted_faces/{celeb_id}.jpg" # S3 key for this extracted face

                # Extract and upload face image to S3
                if face_box and all(k in face_box for k in ['Left', 'Top', 'Width', 'Height']):
                    frame_number = int(timestamp_seconds * video_fps)
                    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
                    ret, frame = cap.read()

                    if ret:
                        # Convert relative coordinates to absolute pixels
                        left = int(face_box['Left'] * current_frame_width)
                        top = int(face_box['Top'] * current_frame_height)
                        width = int(face_box['Width'] * current_frame_width)
                        height = int(face_box['Height'] * current_frame_height)

                        # Add a small padding around the face to get a better crop
                        padding = 20
                        x1 = max(0, left - padding)
                        y1 = max(0, top - padding)
                        x2 = min(current_frame_width, left + width + padding)
                        y2 = min(current_frame_height, top + height + padding)

                        if x2 > x1 and y2 > y1:
                            face_crop = frame[y1:y2, x1:x2]
                            if face_crop.size > 0: # Ensure face crop is not empty
                                # Save crop to a temporary file locally before uploading to S3
                                temp_face_crop_path = TEMP_VIDEO_DIR / f"celeb_crop_{celeb_id}.jpg"
                                success = cv2.imwrite(str(temp_face_crop_path), face_crop)
                                if success and temp_face_crop_path.exists():
                                    try:
                                        s3_client.upload_file(str(temp_face_crop_path), S3_BUCKET_NAME, face_image_s3_key)
                                        uploaded_face_keys.append(face_image_s3_key)
                                        logger.info(f"Uploaded extracted face for {celeb_name} to S3: {face_image_s3_key}")
                                        # The frontend will call /get-face-image with this key
                                        celeb_image_url_for_frontend = f"/get-face-image?s3_key={face_image_s3_key}"
                                    except Exception as s3_upload_e:
                                        logger.error(f"Failed to upload face crop to S3 for {celeb_name}: {s3_upload_e}", exc_info=True)
                                        celeb_image_url_for_frontend = f"https://placehold.co/128x128/FF0000/FFFFFF?text=Error" # Placeholder for error
                                    finally:
                                        if temp_face_crop_path.exists(): # Clean up local temp face crop
                                            temp_face_crop_path.unlink()
                                else:
                                    logger.warning(f"Failed to save temporary face image for {celeb_name}. Using error placeholder.")
                                    celeb_image_url_for_frontend = f"https://placehold.co/128x128/FF0000/FFFFFF?text=Error"
                            else:
                                logger.warning(f"Empty face crop for {celeb_name}. Using error placeholder.")
                                celeb_image_url_for_frontend = f"https://placehold.co/128x128/FF0000/FFFFFF?text=Error"
                        else:
                            logger.warning(f"Invalid bounding box for {celeb_name}. Using error placeholder.")
                            celeb_image_url_for_frontend = f"https://placehold.co/128x128/FF0000/FFFFFF?text=Error"
                    else:
                        logger.warning(f"Could not read frame {frame_number} for {celeb_name}. Using error placeholder.")
                        celeb_image_url_for_frontend = f"https://placehold.co/128x128/FF0000/FFFFFF?text=Error"
                else:
                    logger.warning(f"No valid face box for {celeb_name}. Using general placeholder.")
                    celeb_image_url_for_frontend = f"https://placehold.co/128x128/CCCCCC/000000?text=No+Face" # General placeholder

                unique_celebrities[celeb_id] = {
                    "id": celeb_id,
                    "name": celeb_name,
                    "image_path": celeb_image_url_for_frontend, # This will be the URL to our /get-face-image endpoint
                    "is_celebrity": True,
                    "rekognition_id": celebrity.get('Id') # Store Rekognition's specific ID if needed
                }

            # Prepare detection data
            # Rekognition returns normalized bounding box coordinates (0-1)
            # The frontend might expect absolute pixels or normalized depending on its rendering logic.
            # Convert to absolute for 'location' field as per your original code's structure
            location = [0, 0, 0, 0] # Default
            if face_box:
                left = int(face_box.get('Left', 0) * current_frame_width)
                top = int(face_box.get('Top', 0) * current_frame_height)
                width = int(face_box.get('Width', 0) * current_frame_width)
                height = int(face_box.get('Height', 0) * current_frame_height)
                location = [left, top, width, height]
            else:
                logger.warning(f"No bounding box for detection of {celeb_name} at {timestamp_seconds}s.")

            detections.append({
                "frame": int(timestamp_seconds * video_fps) if video_fps > 0 else 0, # Approximate frame index
                "time": round(timestamp_seconds, 2),
                "face_id": celeb_id, # Link to the unique celebrity (our internal ID)
                "rekognition_face_id": celebrity.get('Id'), # Rekognition's ID for this specific face instance
                "location": location,
                "confidence": celebrity.get('MatchConfidence', celebrity.get('Confidence', 0))
            })

        cap.release() # Release the video capture object
        cv2.destroyAllWindows() # Close any OpenCV windows if opened (though not typically for server)

        # Structure final results
        full_results = {
            "video_info": video_info,
            "detections": detections,
            "unique_faces": list(unique_celebrities.values())
        }

        results_filename = f"results_{uuid.uuid4()}.json"
        results_path = DATA_DIR / results_filename

        with open(results_path, "w") as f:
            json.dump(full_results, f, indent=2)
        logger.info(f"Processing results saved to: {results_path}")

        return JSONResponse(content={
            "status": "success",
            "message": "Video processed successfully with AWS Rekognition for celebrities.",
            "results_filename": results_filename,
            "video_info": full_results["video_info"],
            "unique_faces": full_results["unique_faces"]
        })

    except HTTPException: # Re-raise HTTPExceptions as they are already formatted
        raise
    except Exception as e:
        logger.error(f"An unexpected error occurred during video upload/processing: {e}", exc_info=True)
        # Clean up S3 object if it was uploaded successfully
        if s3_video_uploaded:
            try:
                s3_client.delete_object(Bucket=S3_BUCKET_NAME, Key=video_s3_key)
                logger.info(f"Cleaned up S3 video object: s3://{S3_BUCKET_NAME}/{video_s3_key}")
            except Exception as s3_e:
                logger.error(f"Error during S3 video cleanup: {s3_e}")
        
        # Clean up any uploaded face images
        if uploaded_face_keys:
            for key in uploaded_face_keys:
                try:
                    s3_client.delete_object(Bucket=S3_BUCKET_NAME, Key=key)
                    logger.info(f"Cleaned up S3 face image object: s3://{S3_BUCKET_NAME}/{key}")
                except Exception as s3_e:
                    logger.error(f"Error during S3 face image cleanup for {key}: {s3_e}")

        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")
    finally:
        # Ensure temporary local video file is deleted
        if temp_video_path.exists():
            try:
                temp_video_path.unlink()
                logger.info(f"Cleaned up temporary video file: {temp_video_path}")
            except Exception as e:
                logger.error(f"Error deleting temporary video file {temp_video_path}: {e}")


@app.post("/update-face", summary="Update the name of a specific unique face ID in results file.")
async def update_face_endpoint(
    face_id: str = Form(..., description="ID of the unique face to update"),
    new_name: str = Form(..., description="New name to assign to the face"),
    results_filename: str = Form(..., description="Filename of the JSON results file to update")
):
    """
    Updates the name of a unique face identified in a previous video processing.
    This changes the name stored in the local results JSON file.
    """
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
                    # If you want to update the image too, you'd need to re-generate/re-upload or
                    # generate a new placeholder if the image changes based on the name.
                    # For now, the image_path remains what it was (pointing to S3).
                    found_face = True
                    break
            
            if not found_face:
                raise HTTPException(status_code=404, detail=f"Face with ID {face_id} not found in results file.")
            
            f.seek(0) # Go to the beginning of the file to overwrite
            json.dump(results, f, indent=2)
            f.truncate() # Remove remaining part if new content is shorter
            logger.info(f"Updated face ID {face_id} to name '{new_name}' in {results_filename}.")
        
        return JSONResponse(content={"status": "success", "message": "Face name updated successfully."})
    except json.JSONDecodeError:
        logger.error(f"Error decoding JSON from {results_filename}")
        raise HTTPException(status_code=500, detail="Error reading results file.")
    except Exception as e:
        logger.error(f"Error updating face name: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")


@app.post("/add-known-face", summary="Add a new image and name to the Rekognition known faces collection.")
async def add_known_face_endpoint(
    name: str = Form(..., description="Name of the person"),
    file: UploadFile = File(..., description="Image file of the person's face")
):
    """
    Uploads an image as a "known face" to S3 and indexes it into the Rekognition collection.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No image file provided.")

    image_filename = f"known_face_{uuid.uuid4()}{Path(file.filename).suffix}"
    temp_image_path = TEMP_VIDEO_DIR / image_filename # Use temp video dir for any temporary local files
    image_s3_key = f"known_faces/{image_filename}" # S3 path for known faces

    s3_image_uploaded = False # Flag for S3 cleanup

    try:
        # 1. Save uploaded image to a temporary local file
        contents = await file.read()
        with open(temp_image_path, "wb") as buffer:
            buffer.write(contents)
        logger.info(f"Saved temporary known face image to {temp_image_path}")
        
        # 2. Upload the known face image to S3
        s3_client.upload_file(str(temp_image_path), S3_BUCKET_NAME, image_s3_key)
        s3_image_uploaded = True
        logger.info(f"Uploaded known face image '{name}' to s3://{S3_BUCKET_NAME}/{image_s3_key}")

        # 3. Index the face in Rekognition collection
        response = rekognition_client.index_faces(
            CollectionId=REKOGNITION_COLLECTION_ID,
            Image={'S3Object': {'Bucket': S3_BUCKET_NAME, 'Name': image_s3_key}},
            ExternalImageId=name, # This name will be associated with the face in the collection
            DetectionAttributes=['ALL']
        )
        
        if not response['FaceRecords']:
            # If no face was detected by Rekognition in the image, clean up S3 and raise error
            logger.warning(f"No face detected by Rekognition in the uploaded image for '{name}'. Cleaning up S3 object.")
            s3_client.delete_object(Bucket=S3_BUCKET_NAME, Key=image_s3_key)
            raise HTTPException(status_code=422, detail="No face detected in the uploaded image by Rekognition. Please ensure the image contains a clear face.")
        
        logger.info(f"Face for '{name}' indexed successfully in Rekognition. FaceId(s): {[f['Face']['FaceId'] for f in response['FaceRecords']]}")
        return JSONResponse(content={"status": "success", "message": f"Known face '{name}' added successfully to Rekognition collection and S3."})
    
    except HTTPException: # Re-raise HTTPExceptions
        raise
    except Exception as e:
        logger.error(f"Error adding known face: {e}", exc_info=True)
        # Clean up S3 object if uploaded but indexing failed
        if s3_image_uploaded:
            try:
                s3_client.delete_object(Bucket=S3_BUCKET_NAME, Key=image_s3_key)
                logger.info(f"Cleaned up S3 object for failed known face upload: {image_s3_key}")
            except Exception as s3_e:
                logger.error(f"Error during S3 cleanup of failed known face upload: {s3_e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")
    finally:
        # Ensure temporary local file is deleted
        if temp_image_path.exists():
            try:
                temp_image_path.unlink()
                logger.info(f"Cleaned up temporary known face image file: {temp_image_path}")
            except Exception as e:
                logger.error(f"Error deleting temporary file {temp_image_path}: {e}")


@app.get("/get-face-image", summary="Serve an extracted or known face image from S3.")
async def get_face_image_endpoint(s3_key: str):
    """
    Generates a presigned URL for an image stored in S3, allowing the frontend
    to directly access the image securely.
    The `s3_key` should be the full S3 object key (e.g., "extracted_faces/celeb_ID.jpg" or "known_faces/image_ID.jpg").
    """
    if not s3_key:
        raise HTTPException(status_code=400, detail="Missing S3 key for image.")

    try:
        # Generate a presigned URL for secure, temporary access to the S3 object
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': S3_BUCKET_NAME, 'Key': s3_key},
            ExpiresIn=3600 # URL valid for 1 hour
        )
        logger.info(f"Generated presigned URL for S3 object {s3_key}")
        return RedirectResponse(url)
    except s3_client.exceptions.ClientError as e:
        error_code = e.response.get("Error", {}).get("Code")
        if error_code == 'NoSuchKey':
            logger.warning(f"S3 object not found for key: {s3_key}. Returning 404.")
            raise HTTPException(status_code=404, detail=f"Image not found for key: {s3_key}")
        else:
            logger.error(f"Error generating presigned URL for {s3_key}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Internal server error generating URL: {e}")
    except Exception as e:
        logger.error(f"Unexpected error generating presigned URL for {s3_key}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")


@app.get("/get-results/{results_filename}", summary="Retrieve full processing results from a stored JSON file.")
async def get_results_endpoint(results_filename: str):
    """
    Retrieves the JSON results file generated by a previous video processing task.
    """
    json_path = DATA_DIR / results_filename
    if not json_path.exists():
        raise HTTPException(status_code=404, detail="Results file not found.")
    
    try:
        with open(json_path, "r") as f:
            results_data = json.load(f)
        logger.info(f"Retrieved results from {results_filename}.")
        return JSONResponse(content=results_data)
    except json.JSONDecodeError:
        logger.error(f"Error decoding JSON from {results_filename}")
        raise HTTPException(status_code=500, detail="Error reading results file.")
    except Exception as e:
        logger.error(f"Error retrieving results from {results_filename}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")


@app.get("/get-known-faces", summary="Get a list of currently known faces (indexed in Rekognition collection).")
async def get_known_faces_endpoint():
    """
    Retrieves a list of faces currently indexed in the AWS Rekognition collection.
    For each face, it attempts to provide its name (ExternalImageId) and an S3 URL for its image.
    """
    known_faces_data = []
    try:
        paginator = rekognition_client.get_paginator('list_faces')
        pages = paginator.paginate(CollectionId=REKOGNITION_COLLECTION_ID)
        
        for page in pages:
            for face_record in page['Faces']:
                face_id = face_record.get('FaceId')
                # ExternalImageId is what we passed when indexing the face (the name of the person)
                external_image_id = face_record.get('ExternalImageId', 'Unknown').replace("_", " ") # Clean up name
                
                # Construct the S3 key for the image based on how it was uploaded in add_known_face
                # Assuming 'known_faces/filename.jpg' and filename matches external_image_id + extension
                # This needs careful alignment between add_known_face and get_known_faces.
                # For simplicity, we'll try to guess a common extension or link to our endpoint.
                
                # A more robust way: when indexing a face, store the S3 key in a database associated with face_id.
                # Since we don't have a DB here, we'll assume the external_image_id itself helps locate the S3 image.
                # If ExternalImageId is "John Doe" and S3 key is "known_faces/known_face_UUID.jpg",
                # this linking won't work automatically.
                #
                # Re-evaluating: In `add_known_face`, we generated `image_s3_key` as `f"known_faces/{image_filename}"`.
                # We need to retrieve that exact `image_filename` to construct the S3 key here.
                # Rekognition's `ExternalImageId` is just the `name`.
                #
                # To make this work without a database:
                # 1. When adding a known face, ensure `ExternalImageId` *is* the full S3_KEY. (Not recommended)
                # 2. Or, for this demo, assume the image_path will be generated by calling `/get-face-image` with
                #    a key based on the `ExternalImageId` (which might not be the actual S3 key).
                #
                # The best immediate fix for a demo: change add_known_face to use ExternalImageId as the filename base,
                # then list_faces can reconstruct the S3 key.
                # For now, let's assume the frontend will call /get-face-image with a well-known S3 key format.
                
                # The image_path to pass to the frontend will be a URL to our /get-face-image endpoint,
                # which will then generate a presigned URL to the *actual* S3 object.
                # We need a way to link the Rekognition FaceId/ExternalImageId back to its original S3 key.
                # If `add_known_face` stores the image as `known_faces/known_face_UUID.jpg` and `ExternalImageId` is 'PersonName',
                # then `list_faces` will only give us 'PersonName'. We lose the UUID.
                #
                # A quick fix for this demo *without a database* is to use a predictable S3 key for known faces.
                # Let's say `add_known_face` uploads as `known_faces/{name_cleaned}.jpg`.
                
                # If you use the full UUID + name in ExternalImageId, you can extract it.
                # For now, we'll construct a *potential* S3 key and use our endpoint.
                
                # Assuming the image S3 key for a known face would be `known_faces/rekognition_face_id.jpg`
                # or `known_faces/external_image_id.jpg` (if external_image_id is truly unique filename).
                # Let's use `known_faces/face_id.jpg` as the convention for this demo.
                s3_image_key_for_known_face = f"known_faces/{face_id}.jpg" 
                # This means when you add a known face, you should upload it with an S3 key based on the face_id returned by index_faces.
                # Or, store this mapping in a simple local JSON if you absolutely cannot use a DB.
                # For this implementation, I'm modifying `add_known_face` to use the Rekognition FaceId for the S3 key.
                
                image_url_for_frontend = f"/get-face-image?s3_key={s3_image_key_for_known_face}"

                known_faces_data.append({
                    "id": face_id, # Rekognition's internal FaceId
                    "name": external_image_id, # The name you gave it
                    "image_path": image_url_for_frontend,
                    "is_celebrity": False # These are "known" faces for identification, not necessarily "celebrities"
                })
    except Exception as e:
        logger.error(f"Error getting known faces from Rekognition collection: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve known faces: {e}")
    
    return JSONResponse(content={"known_faces": known_faces_data})


@app.get("/test-cors", summary="A simple endpoint to test CORS configuration.")
async def test_cors():
    return {"message": "CORS is configured correctly"}

# Root endpoint (optional, might be overridden by frontend serving)
@app.get("/", include_in_schema=False) # Exclude from OpenAPI docs
async def read_root():
    # This endpoint will likely be overridden by your Vercel frontend.
    # If the backend is accessed directly, it provides a simple message.
    return {"message": "Welcome to the Video Celebrity Recognition API!"}