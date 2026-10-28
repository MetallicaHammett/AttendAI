import React from 'react';
import { Link as RouterLink, Navigate, Outlet, useParams } from 'react-router-dom';
import { Button } from '@mui/material';
import ArrowBack from '@mui/icons-material/ArrowBack';
import StudentAttendanceReport from '../components/StudentAttendanceReport';
import { PageHeader } from '../components/ui';
import { useAuth } from '../context/AuthContext';

const describe = (student) => (student
  ? [student.student_id, student.name, student.class_name].filter(Boolean).join(' · ')
  : undefined);

// A student account's own page: which student comes from the account, never from the URL
export const MyAttendancePage = () => (
  <StudentAttendanceReport url="/portal/me/attendance"
    header={(student) => <PageHeader title="Attendance Details" subtitle={describe(student)} />} />
);

// Staff view of any student's page (any staff role may view; nothing here changes data)
export const StudentAttendancePage = () => {
  const { id } = useParams();
  return (
    <StudentAttendanceReport url={`/portal/students/${encodeURIComponent(id)}/attendance`}
      header={(student) => (
        <PageHeader title={student ? `Attendance of ${student.name}` : 'Student attendance'} subtitle={describe(student)}
          actions={<Button component={RouterLink} to="/students" startIcon={<ArrowBack />}>All students</Button>} />
      )} />
  );
};

// Route guards. The server enforces the same rules; these only send people to the right page.
export const StaffOnly = () => {
  const { user } = useAuth();
  if (user?.role === 'student') return <Navigate replace to="/my-attendance" />;
  if (user?.role === 'professor') return <Navigate replace to="/staff" />;
  return <Outlet />;
};

export const StudentOnly = () => {
  const { user } = useAuth();
  return user?.role === 'student' ? <Outlet /> : <Navigate replace to="/dashboard" />;
};
