import React, { useState } from 'react';
import { Box, TextField, Button, Typography, Paper, Container, Link, Alert } from '@mui/material';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { getErrorMessage, useRegistrationOpen } from '../data';
import { DemoBanner } from '../components/ui';

const LoginPage = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { login, isAuthenticated, loading, authMessage } = useAuth();
  const [formData, setFormData] = useState({
    username: '',
    password: ''
  });
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const registrationOpen = useRegistrationOpen();

  // Page the user tried to open before being sent here (set by RequireAuth),
  // including its query string and hash
  const requested = location.state?.from;
  const from = requested?.pathname && requested.pathname !== '/login'
    ? `${requested.pathname}${requested.search || ''}${requested.hash || ''}`
    : '/dashboard';
  const registered = location.state?.registered;

  if (!loading && isAuthenticated) {
    return <Navigate to={from} replace />;
  }

  const handleChange = (e) => {
    setFormData({
      ...formData,
      [e.target.name]: e.target.value
    });
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    if (!formData.username.trim() || !formData.password) {
      setError('Enter your username (or email) and password.');
      return;
    }
    setSubmitting(true);
    try {
      await login(formData.username.trim(), formData.password);
      navigate(from, { replace: true });
    } catch (err) {
      // e.g. "Incorrect username or password", "Inactive user", or server unreachable
      setError(getErrorMessage(err, 'Sign in failed'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Container sx={{ minHeight: '100vh' }} component="main" maxWidth="sm">
      <Box sx={{ pt: { xs: 4, sm: 6 }, pb: 4, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
        <Box component="img" src={`${process.env.PUBLIC_URL}/srm-logo.png`}
          alt="SRM Institute of Science and Technology logo"
          sx={{ width: '100%', maxWidth: 520, height: 'auto', mb: 2 }} />
        <Typography align="center" sx={{ fontWeight: 800, fontSize: { xs: 15, sm: 18 }, letterSpacing: '0.05em', color: 'text.primary' }}>
          SRM INSTITUTE OF SCIENCE AND TECHNOLOGY
        </Typography>
        <Typography component="h1" align="center" color="primary"
          sx={{ fontWeight: 900, fontSize: { xs: 40, sm: 52 }, lineHeight: 1.1, mt: 1 }}>
          AttendAI
        </Typography>
        <Typography variant="subtitle1" color="text.secondary" align="center">
          Intelligent Attendance System
        </Typography>
        <Typography variant="body2" color="text.secondary" align="center" gutterBottom>
          Classroom attendance and occupancy · on-premise
        </Typography>
        <DemoBanner sx={{ mt: 3, mb: 0, maxWidth: 400 }} />
        <Paper variant="outlined" sx={{ p: 4, width: '100%', maxWidth: 400, mt: 3 }}>
          <Typography component="h2" variant="h6" align="center" mb={3}>
            Sign In to AttendAI
          </Typography>

          {registered && !error && (
            <Alert severity="success" sx={{ mb: 2 }}>
              Account created. You can sign in now.
            </Alert>
          )}

          {authMessage && !error && (
            <Alert severity="warning" sx={{ mb: 2 }}>
              {authMessage}
            </Alert>
          )}

          {error && (
            <Alert severity="error" sx={{ mb: 2 }}>
              {error}
            </Alert>
          )}

          <Box component="form" onSubmit={handleSubmit} noValidate sx={{ mt: 2 }}>
            <TextField
              label="Username or email"
              name="username"
              value={formData.username}
              onChange={handleChange}
              required
              fullWidth
              sx={{ mb: 2 }}
              autoFocus
              autoComplete="username"
            />
            <TextField
              label="Password"
              name="password"
              type="password"
              value={formData.password}
              onChange={handleChange}
              required
              fullWidth
              sx={{ mb: 3 }}
              autoComplete="current-password"
            />
            <Button
              type="submit"
              fullWidth
              variant="contained"
              color="primary"
              sx={{ mb: 3 }}
              disabled={submitting || loading}
            >
              {submitting ? 'Signing in...' : 'Sign In'}
            </Button>
          </Box>

          <Box sx={{ textAlign: 'center', mt: 2 }}>
            {registrationOpen === undefined ? null : registrationOpen === false ? (
              <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                Need an account? Ask an AttendAI administrator.
              </Typography>
            ) : (
              <Typography sx={{ mb: 1 }}>
                Don't have an account?{' '}
                <Link component="button" type="button" variant="body1" color="primary" onClick={() => navigate('/register')}>
                  Sign up
                </Link>
              </Typography>
            )}
          </Box>
        </Paper>
      </Box>
    </Container>
  );
};

export default LoginPage;
