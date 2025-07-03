import os
import uuid
import json
from pathlib import Path
import time
import cv2
import face_recognition  # New import for local face recognition

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

# --- Configuration and Setup ---

load_dotenv()

app = FastAPI(
    title="Video Face Recognition API (Local OpenCV/Face Recognition)",
    description="API for uploading videos, detecting and recognizing faces locally using OpenCV and face_recognition.",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

# --- Global Variables for Known Faces ---
known_face_encodings = []
known_face_names = []
unique_celebrities_data = [] # To store details for the frontend

@app.on_event("startup")
async def load_known_faces():
    """
    Loads known faces and their encodings from the KNOWN_FACES_DIR.
    """
    print("Loading known faces...")
    for filename in os.listdir(KNOWN_FACES_DIR):
        if filename.lower().endswith((".png", ".jpg", ".jpeg")):
            image_path = str(KNOWN_FACES_DIR / filename)
            image = face_recognition.load_image_file(image_path)
            encodings = face_recognition.face_encodings(image)

            if encodings:
                known_face_encodings.append(encodings[0])
                name = Path(filename).stem.replace("_", " ").title()
                known_face_names.append(name)
                # Prepare data for frontend unique_faces
                unique_celebrities_data.append({
                    "id": str(uuid.uuid5(uuid.NAMESPACE_URL, name)), # Generate stable ID
                    "name": name,
                    "image_path": f"/static/known_faces/{filename}", # Assuming static files for known faces
                    "is_celebrity": True # Or False, depending on your logic
                })
                print(f"Loaded face: {name}")
            else:
                print(f"No face found in {filename}")
    print(f"Finished loading {len(known_face_names)} known faces.")

# Serve static files for known faces and the frontend
app.mount("/static/known_faces", StaticFiles(directory=KNOWN_FACES_DIR), name="known_faces_static")
if FRONTEND_BUILD_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_BUILD_DIR, html=True), name="frontend")
    print(f"Serving frontend from: {FRONTEND_BUILD_DIR}")
else:
    print(f"Frontend build directory not found: {FRONTEND_BUILD_DIR}. Frontend will not be served.")


@app.post("/upload-video")
async def upload_video_endpoint(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No video file provided.")

    video_filename = f"uploaded_{uuid.uuid4()}{Path(file.filename).suffix}"
    temp_video_path = TEMP_VIDEO_DIR / video_filename

    detections = []
    video_info = {
        "fps": 0.0,
        "total_frames": 0,
        "resolution": "0x0",
        "duration_seconds": 0.0
    }
    
    # Keep track of unique identified faces during this video processing
    # This ensures we only add them to unique_faces_list if they were detected
    # in this specific video, and prevents duplicates.
    detected_unique_faces_in_video = {}

    try:
        contents = await file.read()
        with open(temp_video_path, "wb") as buffer:
            buffer.write(contents)

        cap = cv2.VideoCapture(str(temp_video_path))

        if not cap.isOpened():
            raise HTTPException(status_code=500, detail="Could not open video file.")

        frame_rate = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        duration_seconds = total_frames / frame_rate if frame_rate > 0 else 0

        video_info = {
            "fps": float(frame_rate),
            "total_frames": total_frames,
            "resolution": f"{frame_width}x{frame_height}",
            "duration_seconds": duration_seconds
        }

        frame_count = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            frame_count += 1
            current_time_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
            current_time_seconds = current_time_ms / 1000.0

            # Convert the image from BGR (OpenCV) to RGB (face_recognition)
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            # Find all face locations and face encodings in the current frame
            face_locations = face_recognition.face_locations(rgb_frame)
            face_encodings = face_recognition.face_encodings(rgb_frame, face_locations)

            for (top, right, bottom, left), face_encoding in zip(face_locations, face_encodings):
                name = "Unknown"
                face_id = str(uuid.uuid4()) # Unique ID for this specific detection

                # Compare the detected face with known faces
                matches = face_recognition.compare_faces(known_face_encodings, face_encoding)

                face_distances = face_recognition.face_distance(known_face_encodings, face_encoding)
                best_match_index = -1
                if len(face_distances) > 0:
                    best_match_index = min(range(len(face_distances)), key=face_distances.__getitem__)

                if best_match_index != -1 and matches[best_match_index]:
                    name = known_face_names[best_match_index]
                    # Use the stable ID from unique_celebrities_data if it's a known face
                    known_celeb_data = next((item for item in unique_celebrities_data if item["name"] == name), None)
                    if known_celeb_data:
                        face_id = known_celeb_data["id"]
                        detected_unique_faces_in_video[face_id] = known_celeb_data
                    else: # Fallback, should not happen if known faces are loaded properly
                        detected_unique_faces_in_video[face_id] = {
                            "id": face_id,
                            "name": name,
                            "image_path": "", # Will be filled if we save the crop
                            "is_celebrity": True # Assume known are celebrities for this demo
                        }
                else:
                    # Save unknown faces for review or future training
                    unknown_face_id = str(uuid.uuid4())
                    face_image = frame[top:bottom, left:right]
                    face_save_path = FACES_SAVE_DIR / f"unknown_{unknown_face_id}.jpg"
                    cv2.imwrite(str(face_save_path), face_image)
                    face_id = unknown_face_id
                    detected_unique_faces_in_video[face_id] = {
                        "id": face_id,
                        "name": "Unknown",
                        "image_path": f"/static/faces/unknown_{unknown_face_id}.jpg",
                        "is_celebrity": False
                    }

                detections.append({
                    "timestamp": current_time_seconds,
                    "frame_index": frame_count,
                    "face_id": face_id,
                    "name": name,
                    "box": {
                        "Left": left,
                        "Top": top,
                        "Width": right - left,
                        "Height": bottom - top
                    }
                })
        
        cap.release()

        # Convert the dictionary of detected unique faces to a list
        final_unique_faces_in_video_list = list(detected_unique_faces_in_video.values())

        results_filename = f"results_{uuid.uuid4()}.json"
        results_path = DATA_DIR / results_filename

        full_results = {
            "video_info": video_info,
            "detections": detections,
            "unique_faces": final_unique_faces_in_video_list
        }

        with open(results_path, "w") as f:
            json.dump(full_results, f, indent=2)

        return JSONResponse(content={
            "status": "success",
            "message": "Video uploaded and processed locally.",
            "results_filename": results_filename,
            "video_info": full_results["video_info"],
            "unique_faces": full_results["unique_faces"]
        })

    except Exception as e:
        print(f"Error during video processing: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")
    finally:
        if temp_video_path.exists():
            try:
                temp_video_path.unlink()
            except Exception:
                pass

# Serve detected faces as static files
app.mount("/static/faces", StaticFiles(directory=FACES_SAVE_DIR), name="faces_static")

@app.get("/results/{results_filename}")
async def get_results(results_filename: str):
    results_path = DATA_DIR / results_filename
    if not results_path.exists():
        raise HTTPException(status_code=404, detail="Results file not found.")
    
    with open(results_path, "r") as f:
        results = json.load(f)
    return JSONResponse(content=results)

@app.get("/")
async def redirect_to_frontend():
    # This is a fallback if the static files aren't mounted correctly,
    # or if you want a direct entry point.
    if FRONTEND_BUILD_DIR.is_dir():
        return RedirectResponse(url="/index.html")
    return JSONResponse(content={"message": "Welcome to the Face Recognition API! Frontend not found."})

# Example of how you might add a route to manage known faces (optional)
@app.post("/add-known-face")
async def add_known_face(file: UploadFile = File(...), name: str = Form(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No image file provided.")

    clean_name = name.strip().replace(" ", "_").lower()
    save_path = KNOWN_FACES_DIR / f"{clean_name}{Path(file.filename).suffix}"
    
    try:
        contents = await file.read()
        with open(save_path, "wb") as buffer:
            buffer.write(contents)

        # Reload known faces to include the new one
        await load_known_faces() 
        return JSONResponse(content={"status": "success", "message": f"Added {name} to known faces."})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add known face: {e}")
