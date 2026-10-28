import React, { useState, useEffect } from 'react';
import axios from 'axios';

function HealthCheck() {
  const [backendStatus, setBackendStatus] = useState('checking...');
  const [backendData, setBackendData] = useState(null);

  useEffect(() => {
    const checkBackend = async () => {
      setBackendStatus('checking...');
      try {
        const response = await axios.get('http://localhost:8000/health');
        setBackendStatus('connected');
        setBackendData(response.data);
      } catch (error) {
        setBackendStatus('disconnected');
        setBackendData(null);
        console.error('Failed to connect to backend:', (error as Error).message);
      }
    };

    checkBackend();

    // Poll every 5 seconds
    const interval = setInterval(checkBackend, 5000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div>
      <h2>Backend Connection Status</h2>
      <p>Status: <span className={backendStatus === 'connected' ? 'status-success' : 'status-error'}>
        {backendStatus}
      </span></p>
      {backendData && (
        <div>
          <h3>Backend Response:</h3>
          <pre>{JSON.stringify(backendData, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}

export default HealthCheck;