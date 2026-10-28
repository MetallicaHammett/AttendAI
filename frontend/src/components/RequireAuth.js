import React from 'react';
import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { Box, CircularProgress } from '@mui/material';
import { useAuth } from '../context/AuthContext';

// Guards protected routes. Used as a layout route (<Route element={<RequireAuth />}>),
// so it must render <Outlet /> for the nested pages; it also accepts children.
const RequireAuth = ({ children }) => {
  const { isAuthenticated, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', mt: 8 }} role="status" aria-label="Checking session">
        <CircularProgress />
      </Box>
    );
  }

  if (!isAuthenticated) {
    // Remember where the user was going so login can send them back
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  return children ?? <Outlet />;
};

export default RequireAuth;
