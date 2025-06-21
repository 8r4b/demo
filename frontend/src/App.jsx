import React, { useState, useEffect, useRef } from 'react';
import './style.css';

function App() {
    const [selectedVideoFile, setSelectedVideoFile] = useState(null);
    const [statusMessage, setStatusMessage] = useState({ text: '', type: 'info' });
    const [currentResults, setCurrentResults] = useState(null);
    const [currentResultsFilename, setCurrentResultsFilename] = useState(null);
    const [searchQuery, setSearchQuery] = useState('');
    const [knownFaces, setKnownFaces] = useState([]);
    const [newKnownFaceName, setNewKnownFaceName] = useState('');
    const [newKnownFaceImage, setNewKnownFaceImage] = useState(null);
    const [selectedFaceForModal, setSelectedFaceForModal] = useState(null);
    const [modalFaceName, setModalFaceName] = useState('');

    const faceModalRef = useRef(null);
    const bootstrapModalInstance = useRef(null);

    const API_BASE_URL = "https://test-production-7775.up.railway.app";

    useEffect(() => {
        if (window.bootstrap && faceModalRef.current) {
            bootstrapModalInstance.current = new window.bootstrap.Modal(faceModalRef.current);
        }
        return () => {
            if (bootstrapModalInstance.current) {
                bootstrapModalInstance.current.dispose();
                bootstrapModalInstance.current = null;
            }
        };
    }, []);

    useEffect(() => {
        renderKnownFaces();
    }, []);

    const showStatus = (text, type = 'info') => {
        setStatusMessage({ text, type });
    };

    const formatTime = (seconds) => {
        const minutes = Math.floor(seconds / 60);
        const remainingSeconds = Math.floor(seconds % 60);
        return `${String(minutes).padStart(2, '0')}:${String(remainingSeconds).padStart(2, '0')}`;
    };

    const renderKnownFaces = async () => {
        try {
            const response = await fetch(`${API_BASE_URL}/get-known-faces`);
            if (!response.ok) throw new Error();
            const data = await response.json();
            setKnownFaces(data.known_faces);
        } catch {
            showStatus('Error loading known faces.', 'danger');
        }
    };

    const handleProcessVideo = async () => {
        if (!selectedVideoFile) {
            showStatus('Please select a video file first.', 'warning');
            return;
        }
        setStatusMessage({ text: 'Uploading and processing video...', type: 'info' });
        const formData = new FormData();
        formData.append('file', selectedVideoFile);
        try {
            const response = await fetch(`${API_BASE_URL}/upload-video`, {
                method: 'POST',
                body: formData,
            });
            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || 'Failed to process video.');
            }
            const data = await response.json();
            setCurrentResultsFilename(data.results_filename);
            showStatus('Video processed successfully! Loading results...', 'success');
            await loadAndDisplayResults(data.results_filename);
            if (selectedVideoFile) {
                const videoPlayer = document.getElementById('videoPlayer');
                if (videoPlayer) {
                    videoPlayer.src = URL.createObjectURL(selectedVideoFile);
                    videoPlayer.load();
                }
            }
        } catch (error) {
            showStatus(`Error: ${error.message}`, 'danger');
            setCurrentResults(null);
            setCurrentResultsFilename(null);
            const videoPlayer = document.getElementById('videoPlayer');
            if (videoPlayer) videoPlayer.src = '';
        }
    };

    const loadAndDisplayResults = async (filename) => {
        try {
            const response = await fetch(`${API_BASE_URL}/get-results/${filename}`);
            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || 'Failed to load results.');
            }
            const data = await response.json();
            setCurrentResults(data);
        } catch (error) {
            showStatus(`Error loading results: ${error.message}`, 'danger');
            setCurrentResults(null);
            setCurrentResultsFilename(null);
        }
    };

    const handleSaveFaceName = async () => {
        if (!selectedFaceForModal || !currentResultsFilename) return;
        const newName = modalFaceName.trim();
        if (!newName) {
            showStatus('Face name cannot be empty.', 'warning');
            return;
        }
        if (newName === selectedFaceForModal.name) {
            bootstrapModalInstance.current.hide();
            return;
        }
        showStatus('Saving new name...', 'info');
        try {
            const formData = new FormData();
            formData.append('face_id', selectedFaceForModal.id);
            formData.append('new_name', newName);
            formData.append('results_filename', currentResultsFilename);
            const response = await fetch(`${API_BASE_URL}/update-face`, {
                method: 'POST',
                body: formData,
            });
            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || 'Failed to update face name.');
            }
            setCurrentResults(prevResults => {
                if (!prevResults) return null;
                const updatedUniqueFaces = prevResults.unique_faces.map(face =>
                    face.id === selectedFaceForModal.id ? { ...face, name: newName, image_path: face.is_mock ? `https://placehold.co/128x128/FF5733/ffffff?text=${newName.replace(" ", "%20")}` : face.image_path } : face
                );
                return { ...prevResults, unique_faces: updatedUniqueFaces };
            });
            showStatus('Face name updated successfully!', 'success');
            bootstrapModalInstance.current.hide();
        } catch (error) {
            showStatus(`Error updating name: ${error.message}`, 'danger');
        }
    };

    const handleAddKnownFace = async (event) => {
        event.preventDefault();
        if (!newKnownFaceName || !newKnownFaceImage) {
            showStatus('Please provide both a name and an image for the known face.', 'warning');
            return;
        }
        showStatus(`Adding known face: ${newKnownFaceName}...`, 'info');
        const formData = new FormData();
        formData.append('name', newKnownFaceName);
        formData.append('file', newKnownFaceImage);
        try {
            const response = await fetch(`${API_BASE_URL}/add-known-face`, {
                method: 'POST',
                body: formData,
            });
            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || 'Failed to add known face.');
            }
            showStatus(`Successfully added known face: ${newKnownFaceName}!`, 'success');
            setNewKnownFaceName('');
            setNewKnownFaceImage(null);
            document.getElementById('faceImage').value = '';
            renderKnownFaces();
        } catch (error) {
            showStatus(`Error adding known face: ${error.message}`, 'danger');
        }
    };

    const filteredFaces = currentResults?.unique_faces.filter(face =>
        face.name.toLowerCase().includes(searchQuery.toLowerCase())
    ) || [];

    const videoDuration = currentResults?.video_info?.duration_seconds || 0;

    return (
        <div className="container mx-auto py-8 px-4 main-container">
            <h1 className="mb-6 text-4xl font-bold text-center text-gray-800 heading-gradient">
                Video Face Recognition System
            </h1>
            <div className="card-fancy mb-8">
                <div className="card-header-fancy bg-blue-600">
                    <i className="bi bi-upload text-xl"></i>
                    <h5 className="text-xl font-semibold">Video Upload</h5>
                </div>
                <div className="p-6">
                    <div className="mb-4">
                        <label htmlFor="videoUpload" className="block text-gray-700 text-sm font-medium mb-2">Select Video File</label>
                        <input
                            type="file"
                            id="videoUpload"
                            accept="video/*"
                            className="form-control-fancy block w-full"
                            onChange={(e) => setSelectedVideoFile(e.target.files[0])}
                        />
                    </div>
                    <button
                        onClick={handleProcessVideo}
                        className="btn-primary py-2 px-4 rounded-lg w-full"
                        disabled={!selectedVideoFile}
                    >
                        <i className="bi bi-gear"></i> <span>Process Video</span>
                    </button>
                    {statusMessage.text && (
                        <div className={`status-message mt-4 text-sm ${statusMessage.type === 'success' ? 'text-green-600' : statusMessage.type === 'danger' ? 'text-red-600' : 'text-gray-600'}`}>
                            {statusMessage.text}
                        </div>
                    )}
                </div>
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
                <div className="lg:col-span-2">
                    <div className="card-fancy mb-8">
                        <div className="card-header-fancy bg-blue-600">
                            <i className="bi bi-camera-video text-xl"></i>
                            <h5 className="text-xl font-semibold">Video Preview</h5>
                        </div>
                        <div className="p-6">
                            <video id="videoPlayer" controls className="w-full rounded-md shadow-md"></video>
                            <div className="mt-6">
                                <h6 className="text-lg font-semibold text-gray-800 mb-2">
                                    Timeline ({selectedVideoFile ? selectedVideoFile.name : 'No Video Loaded'})
                                </h6>
                                <div id="timeline" className="timeline-bar">
                                    <div className="timeline-playhead" style={{ '--playhead-position': `${(document.getElementById('videoPlayer')?.currentTime / videoDuration) * 100 || 0}%` }}></div>
                                    {currentResults?.detections.map((detection, index) => (
                                        <div
                                            key={index}
                                            className="timeline-marker"
                                            style={{ left: `${(detection.time / videoDuration) * 100}%` }}
                                            title={`Detected at ${formatTime(detection.time)}`}
                                            onClick={() => {
                                                const videoPlayer = document.getElementById('videoPlayer');
                                                if (videoPlayer) {
                                                    videoPlayer.currentTime = detection.time;
                                                    if (videoPlayer.paused) videoPlayer.play();
                                                }
                                            }}
                                        ></div>
                                    ))}
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
                <div className="lg:col-span-1">
                    <div className="card-fancy mb-8">
                        <div className="card-header-fancy bg-blue-600">
                            <i className="bi bi-people text-xl"></i>
                            <h5 className="text-xl font-semibold">Detected Faces in Video</h5>
                        </div>
                        <div className="p-6">
                            <div className="mb-4">
                                <input
                                    type="text"
                                    id="searchFaces"
                                    placeholder="Search faces..."
                                    className="form-control-fancy block w-full"
                                    value={searchQuery}
                                    onChange={(e) => setSearchQuery(e.target.value)}
                                />
                            </div>
                            <div id="facesList" className="faces-grid">
                                {filteredFaces.length === 0 ? (
                                    <p className="col-span-full text-center text-gray-500">
                                        {currentResults?.unique_faces.length > 0 ? 'No faces match your search.' : 'Upload a video to see detected faces.'}
                                    </p>
                                ) : (
                                    filteredFaces.map((face) => (
                                        <div
                                            key={face.id}
                                            className="face-card"
                                            onClick={() => {
                                                setSelectedFaceForModal(face);
                                                setModalFaceName(face.name);
                                                bootstrapModalInstance.current.show();
                                            }}
                                        >
                                            <img
                                                src={face.image_path.startsWith('faces/') ? `${API_BASE_URL}/${face.image_path}` : face.image_path}
                                                alt={face.name}
                                                className="rounded-t-lg"
                                                onError={(e) => { e.target.onerror = null; e.target.src = 'https://placehold.co/128x128/cccccc/ffffff?text=Error'; }}
                                            />
                                            <div className="face-card-body">
                                                <p className="font-semibold text-gray-800">{face.name}</p>
                                            </div>
                                        </div>
                                    ))
                                )}
                            </div>
                        </div>
                    </div>
                    <div className="card-fancy">
                        <div className="card-header-fancy bg-purple-600">
                            <i className="bi bi-person-plus text-xl"></i>
                            <h5 className="text-xl font-semibold">Manage Known Faces</h5>
                        </div>
                        <div className="p-6">
                            <form onSubmit={handleAddKnownFace} className="mb-6">
                                <div className="mb-4">
                                    <label htmlFor="faceName" className="block text-gray-700 text-sm font-medium mb-2">Person's Name</label>
                                    <input
                                        type="text"
                                        id="faceName"
                                        className="form-control-fancy block w-full"
                                        value={newKnownFaceName}
                                        onChange={(e) => setNewKnownFaceName(e.target.value)}
                                        required
                                    />
                                </div>
                                <div className="mb-4">
                                    <label htmlFor="faceImage" className="block text-gray-700 text-sm font-medium mb-2">Face Image</label>
                                    <input
                                        type="file"
                                        id="faceImage"
                                        accept="image/*"
                                        className="form-control-fancy block w-full"
                                        onChange={(e) => setNewKnownFaceImage(e.target.files[0])}
                                        required
                                    />
                                </div>
                                <button type="submit" className="btn-gradient-purple py-2 px-4 rounded-lg w-full">
                                    <i className="bi bi-plus-circle"></i> <span>Add Known Face</span>
                                </button>
                            </form>
                            <div id="knownFacesList">
                                <h6 className="text-lg font-semibold text-gray-800 mb-3">Currently Known Faces:</h6>
                                <div className="space-y-3" id="knownFacesContainer">
                                    {knownFaces.length === 0 ? (
                                        <p className="text-center text-gray-500">No known faces yet.</p>
                                    ) : (
                                        knownFaces.map((name, index) => (
                                            <div key={index} className="known-face-item">
                                                <img
                                                    src={`https://placehold.co/48x48/6366f1/ffffff?text=${name.split(' ')[0].charAt(0).toUpperCase()}`}
                                                    alt={name}
                                                    className="rounded-full"
                                                />
                                                <span className="text-gray-800 font-medium">{name}</span>
                                            </div>
                                        ))
                                    )}
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            <div className="modal fade" id="faceModal" tabIndex="-1" aria-labelledby="faceModalLabel" aria-hidden="true" ref={faceModalRef}>
                <div className="modal-dialog modal-dialog-centered">
                    <div className="modal-content rounded-lg shadow-xl">
                        <div className="modal-header bg-blue-600">
                            <h5 className="modal-title text-xl font-semibold" id="faceModalTitle">Face Details</h5>
                            <div className="modal-header-blob"></div>
                            <button type="button" className="btn-close btn-close-white btn-close-fancy" data-bs-dismiss="modal" aria-label="Close"></button>
                        </div>
                        <div className="modal-body p-6">
                            {selectedFaceForModal && (
                                <>
                                    <div className="text-center mb-6">
                                        <img
                                            id="modalFaceImage"
                                            src={selectedFaceForModal.image_path.startsWith('faces/') ? `${API_BASE_URL}/${selectedFaceForModal.image_path}` : selectedFaceForModal.image_path}
                                            alt="Detected Face"
                                            className="modal-face-img mx-auto"
                                            onError={(e) => { e.target.onerror = null; e.target.src = 'https://placehold.co/150x150/cccccc/ffffff?text=Error'; }}
                                        />
                                    </div>
                                    <div className="mb-6">
                                        <label htmlFor="modalFaceName" className="block text-gray-700 text-sm font-medium mb-2">Name</label>
                                        <div className="flex">
                                            <input
                                                type="text"
                                                id="modalFaceName"
                                                className="form-control-fancy block w-full rounded-l-lg"
                                                value={modalFaceName}
                                                onChange={(e) => setModalFaceName(e.target.value)}
                                            />
                                            <button
                                                className="btn-save-fancy py-2 px-4 rounded-r-lg"
                                                onClick={handleSaveFaceName}
                                            >
                                                <i className="bi bi-check"></i> <span>Save</span>
                                            </button>
                                        </div>
                                    </div>
                                    <div>
                                        <h6 className="text-lg font-semibold text-gray-800 mb-3">Appearances in Video:</h6>
                                        <ul className="list-group list-group-fancy max-h-48 overflow-y-auto">
                                            {currentResults?.detections.filter(d => d.face_id === selectedFaceForModal.id).sort((a, b) => a.time - b.time).map((appearance, index) => (
                                                <li
                                                    key={index}
                                                    className="list-group-item-fancy text-gray-700"
                                                    onClick={() => {
                                                        const videoPlayer = document.getElementById('videoPlayer');
                                                        if (videoPlayer) {
                                                            videoPlayer.currentTime = appearance.time;
                                                            if (videoPlayer.paused) videoPlayer.play();
                                                        }
                                                        bootstrapModalInstance.current.hide();
                                                    }}
                                                >
                                                    At {formatTime(appearance.time)}
                                                </li>
                                            ))}
                                            {currentResults?.detections.filter(d => d.face_id === selectedFaceForModal.id).length === 0 && (
                                                <li className="list-group-item-fancy text-gray-700">No appearances recorded.</li>
                                            )}
                                        </ul>
                                    </div>
                                </>
                            )}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}

export default App;
