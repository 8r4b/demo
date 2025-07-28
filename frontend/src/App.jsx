import React, { useState, useRef } from 'react';
import './style.css';

export default function App() {
  const [videoFile, setVideoFile] = useState(null);
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const videoRef = useRef(null);
  const fileInputRef = useRef(null);

  const handleFileChange = (e) => {
    const file = e.target.files[0];
    if (file) {
      setVideoFile(file);
      setResults(null);
      videoRef.current.src = URL.createObjectURL(file);
    }
  };

  const processVideo = async () => {
    if (!videoFile) {
      alert('Please select a video first');
      return;
    }

    setLoading(true);
    const formData = new FormData();
    formData.append('file', videoFile);

    try {
      const response = await fetch('https://demo-w1b3.onrender.com/upload-video', {
        method: 'POST',
        body: formData,
      });
      const data = await response.json();
      setResults(data);
    } catch (error) {
      alert('Error processing video');
      console.error(error);
    } finally {
      setLoading(false);
    }
  };

  console.log('App is rendering');

  return (
    <div className="app">
      <header>
        <h1>Celebrity Recognition System</h1>
      </header>

      <main>
        <div className="video-container">
          <video
            ref={videoRef}
            controls
            className={videoFile ? 'visible' : 'hidden'}
          />
          {!videoFile && <p className="upload-prompt">Select a video to preview</p>}
        </div>

        <div className="controls">
          <input
            type="file"
            accept="video/*"
            onChange={handleFileChange}
            ref={fileInputRef}
            className="file-input"
          />
          <button onClick={() => fileInputRef.current.click()}>
            Select Video
          </button>
          <button
            onClick={processVideo}
            disabled={!videoFile || loading}
            className="process-btn"
          >
            {loading ? 'Processing...' : 'Analyze Video'}
          </button>
        </div>

        {results && (
          <div className="results">
            <h2>Detection Results</h2>
            <div className="video-info">
              <p>Duration: {results.video_info?.duration_seconds?.toFixed(2)}s</p>
              <p>Resolution: {results.video_info?.resolution}</p>
            </div>

            <div className="celeb-grid">
              {results.unique_faces?.map((celeb) => (
                <div key={celeb.id} className="celeb-card">
                  <img
                    src={`https://demo-w1b3.onrender.com/${celeb.image_path}`}
                    alt={celeb.name}
                    onError={(e) => {
                      e.target.src = 'https://placehold.co/200x200?text=No+Image';
                    }}
                  />
                  <div className="celeb-info">
                    <h3>{celeb.name}</h3>
                    <p>Confidence: {celeb.confidence?.toFixed(2)}%</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}