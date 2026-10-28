import React from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { CssBaseline } from '@mui/material';
import { ThemeProvider } from '@mui/material/styles';
import LoginPage from './pages/LoginPage';
import RegisterPage from './pages/RegisterPage';
import Dashboard from './pages/Dashboard';
import StudentsPage from './pages/StudentsPage';
import CamerasPage from './pages/CamerasPage';
import AttendancePage from './pages/AttendancePage';
import LiveMonitoringPage from './pages/LiveMonitoringPage';
import ReportsPage from './pages/ReportsPage';
import TimetablePage from './pages/TimetablePage';
import UsersPage from './pages/UsersPage';
import { MyAttendancePage, StaffOnly, StudentAttendancePage, StudentOnly } from './pages/StudentAttendancePages';
import { AttendanceHistoryPage, ClassRegisterPage, ProfessorOnly, WeeklyTimetablePage } from './pages/StaffPortalPages';
import RequireAuth from './components/RequireAuth';
import Layout from './components/Layout';
import { AuthContextProvider } from './context/AuthContext';
import { NotifyProvider } from './context/NotifyContext';
import theme from './theme';

// Routes only; kept separate so tests can render them inside a MemoryRouter
export const AppRoutes = () => (
  <Routes>
    <Route path="/login" element={<LoginPage />} />
    <Route path="/register" element={<RegisterPage />} />
    <Route element={<RequireAuth />}>
      <Route element={<Layout />}>
        <Route element={<StaffOnly />}>
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/students" element={<StudentsPage />} />
          <Route path="/students/:id/attendance" element={<StudentAttendancePage />} />
          <Route path="/cameras" element={<CamerasPage />} />
          <Route path="/attendance" element={<AttendancePage />} />
          <Route path="/live-monitoring" element={<LiveMonitoringPage />} />
          <Route path="/reports" element={<ReportsPage />} />
          <Route path="/timetable" element={<TimetablePage />} />
          <Route path="/users" element={<UsersPage />} />
        </Route>
        <Route element={<StudentOnly />}>
          <Route path="/my-attendance" element={<MyAttendancePage />} />
        </Route>
        <Route element={<ProfessorOnly />}>
          <Route path="/staff" element={<Navigate replace to="/staff/timetable" />} />
          <Route path="/staff/timetable" element={<WeeklyTimetablePage />} />
          <Route path="/staff/register/:periodId/:date" element={<ClassRegisterPage />} />
          <Route path="/staff/history" element={<AttendanceHistoryPage />} />
        </Route>
      </Route>
    </Route>
    {/* "/" and unknown paths: dashboard if signed in, otherwise RequireAuth sends to /login */}
    <Route path="/" element={<Navigate replace to="/dashboard" />} />
    <Route path="*" element={<Navigate replace to="/dashboard" />} />
  </Routes>
);

function App() {
  return (
    <ThemeProvider theme={theme}>
      <AuthContextProvider>
        <NotifyProvider>
          <CssBaseline />
          <Router future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
            <AppRoutes />
          </Router>
        </NotifyProvider>
      </AuthContextProvider>
    </ThemeProvider>
  );
}

export default App;
