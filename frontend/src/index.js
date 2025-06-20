import React from 'react';
import ReactDOM from 'react-dom/client'; // Import from react-dom/client for React 18+
import './style.css'; // Assuming you might have a global index.css for basic styles
import App from './App'; // Import the main App component

// Create a root for rendering your React application.
// This is the recommended way to render React 18+ applications.
const root = ReactDOM.createRoot(document.getElementById('root'));

// Render the App component into the root.
root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);

// If you want to start measuring performance in your app, pass a function
// to log results (for example: reportWebVitals(console.log))
// or send to an analytics endpoint. Learn more: https://bit.ly/CRA-vitals
// You might have a reportWebVitals.js file if this was created by Create React App
// import reportWebVitals from './reportWebVitals';
// reportWebVitals();
