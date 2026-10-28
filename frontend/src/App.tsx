import React from 'react';
import './index.css';
import HealthCheck from './components/HealthCheck';

function App() {
  return (
    <div className="App">
      <header className="App-header">
        <h1>AttendAI</h1>
        <p>Intelligent AI Video-Based Attendance System</p>
      </header>
      <main>
        <HealthCheck />
      </main>
    </div>
  );
}

export default App;