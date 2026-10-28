import React, { useState } from 'react';
import { Box, TextField, Button, Typography, Paper, Container, Link, Alert } from '@mui/material';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { getErrorMessage, useRegistrationOpen } from '../data';

// Mirrors backend schemas.UserCreate: email <= 255, username <= 50, password >= 8
const MIN_PASSWORD_LENGTH = 8;

const validate = ({ email, username, password }) => {
  if (!email.trim() || !username.trim() || !password) return 'All fields are required.';
  if (!/^[^\s@]+@[^\s@]+$/.test(email.trim())) return 'Enter a valid email address.';
  if (email.trim().length > 255) return 'Email must be at most 255 characters.';
  if (username.trim().length > 50) return 'Username must be at most 50 characters.';
  if (password.length < MIN_PASSWORD_LENGTH) return `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`;
  return '';
};

const RegisterPage = () => {
  const navigate = useNavigate();
  const { register } = useAuth();
  const [formData, setFormData] = useState({
    email: '',
    username: '',
    password: ''
  });
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const registrationOpen = useRegistrationOpen();

  const handleChange = (e) => {
    setFormData({
      ...formData,
      [e.target.name]: e.target.value
    });
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    const validationError = validate(formData);
    setError(validationError);
    if (validationError) return;

    setSubmitting(true);
    try {
      await register({
        email: formData.email.trim(),
        username: formData.username.trim(),
        password: formData.password
      });
      // No email confirmation exists; the account can be used immediately
      navigate('/login', { replace: true, state: { registered: true } });
    } catch (err) {
      // e.g. "Email already registered", "Username already taken"
      setError(getErrorMessage(err, 'Registration failed'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Container sx={{ minHeight: '100vh' }} component="main" maxWidth="xs">
      <Box sx={{ pt: { xs: 6, sm: 10 }, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
        <Typography component="h1" variant="h5" align="center" color="text.primary" gutterBottom>
          AttendAI
        </Typography>
        <Paper variant="outlined" sx={{ p: 4, width: '100%', maxWidth: 400, mt: 2 }}>
          <Typography component="h2" variant="h6" align="center" mb={1}>
            Create AttendAI Account
          </Typography>
          <Typography variant="body2" color="text.secondary" align="center" mb={3}>
            New accounts can view dashboards and reports. An administrator grants operator or admin access.
          </Typography>

          {error && (
            <Alert severity="error" sx={{ mb: 2 }}>
              {error}
            </Alert>
          )}

          {registrationOpen === undefined ? (
            <Typography variant="body2" color="text.secondary" align="center" role="status">Checking sign-up availability…</Typography>
          ) : registrationOpen === false ? (
            <Alert severity="info" sx={{ mb: 2 }}>
              Self-registration is turned off on this server. Ask an AttendAI administrator to create your account.
            </Alert>
          ) : (
          <Box component="form" onSubmit={handleSubmit} noValidate sx={{ mt: 2 }}>
            <TextField
              label="Email Address"
              name="email"
              type="email"
              value={formData.email}
              onChange={handleChange}
              required
              fullWidth
              sx={{ mb: 2 }}
              autoComplete="email"
            />
            <TextField
              label="Username"
              name="username"
              value={formData.username}
              onChange={handleChange}
              required
              fullWidth
              sx={{ mb: 2 }}
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
              helperText={`At least ${MIN_PASSWORD_LENGTH} characters`}
              sx={{ mb: 2 }}
              autoComplete="new-password"
            />
            <Button
              type="submit"
              fullWidth
              variant="contained"
              color="primary"
              sx={{ mb: 3 }}
              disabled={submitting}
            >
              {submitting ? 'Creating account...' : 'Register'}
            </Button>
          </Box>
          )}

          <Box sx={{ textAlign: 'center', mt: 2 }}>
            <Typography sx={{ mb: 1 }}>
              Already have an account?{' '}
              <Link component="button" type="button" variant="body1" color="primary" onClick={() => navigate('/login')}>
                Sign in
              </Link>
            </Typography>
          </Box>
        </Paper>
      </Box>
    </Container>
  );
};

export default RegisterPage;
