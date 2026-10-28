import React, { useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  LinearProgress,
  Typography
} from '@mui/material';
import { api, getErrorMessage } from '../data';

// Mirrors backend routers/face_enrollment.py (MAX_FILE_SIZE, ALLOWED_IMAGE_TYPES)
export const MAX_FACE_IMAGE_BYTES = 5 * 1024 * 1024;
export const ALLOWED_FACE_IMAGE_TYPES = ['image/jpeg', 'image/png'];

const checkFile = (file) => {
  if (!ALLOWED_FACE_IMAGE_TYPES.includes(file.type)) return 'Choose a JPEG or PNG image.';
  if (file.size > MAX_FACE_IMAGE_BYTES) return 'The image is larger than 5 MB.';
  if (file.size === 0) return 'The image file is empty.';
  return '';
};

// Uploads one photo to POST /face-enrollment/{student_id} (multipart field "file").
// The backend requires exactly one detectable face; each upload adds an encoding.
const FaceEnrollmentDialog = ({ open, student, onClose, onEnrolled }) => {
  const [file, setFile] = useState(null);
  const [previewUrl, setPreviewUrl] = useState('');
  const [error, setError] = useState('');
  const [uploading, setUploading] = useState(false);
  const [done, setDone] = useState(false);
  const [warning, setWarning] = useState(''); // 409: looks like someone else / unlike earlier photos
  const [result, setResult] = useState(null);

  // One object URL per selected file, revoked when the file changes or the dialog unmounts
  useEffect(() => {
    if (!file) {
      setPreviewUrl('');
      return undefined;
    }
    const url = URL.createObjectURL(file);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  useEffect(() => {
    if (open) {
      setFile(null);
      setError('');
      setDone(false);
      setWarning('');
      setResult(null);
    }
  }, [open, student]);

  const handleClose = () => {
    if (uploading) return;
    setFile(null);
    onClose();
  };

  const handleFileChange = (e) => {
    const selected = e.target.files && e.target.files[0];
    e.target.value = ''; // allow re-selecting the same file after an error
    setDone(false);
    setWarning('');
    if (!selected) return;
    const problem = checkFile(selected);
    setError(problem);
    setFile(problem ? null : selected);
  };

  const handleUpload = async (confirmWarnings = false) => {
    if (!file) return;
    setUploading(true);
    setError('');
    try {
      const body = new FormData();
      body.append('file', file);
      const response = await api.post(`/face-enrollment/${student.id}`, body,
        confirmWarnings ? { params: { confirm_warnings: true } } : undefined);
      setDone(true);
      setWarning('');
      setResult(response.data);
      setFile(null);
      onEnrolled?.(student, response.data);
    } catch (err) {
      if (err?.response?.status === 409 && !confirmWarnings) {
        // The face resembles another enrolled person, or differs from this person's photos
        setWarning(getErrorMessage(err, 'Please check the photo'));
      } else {
        // e.g. "No face detected", "Photo not usable for enrollment: The face is blurred",
        // 503 when face recognition is unavailable on the server
        setError(getErrorMessage(err, 'Face enrollment failed'));
      }
    } finally {
      setUploading(false);
    }
  };

  if (!student) return null;

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
      <DialogTitle>Enroll face: {student.name}</DialogTitle>
      <DialogContent>
        <DialogContentText sx={{ mb: 2 }}>
          Upload a clear, front-facing photo of {student.name} ({student.student_id}) with exactly one face.
          JPEG or PNG, up to 5 MB. Each upload adds another reference image for this student.
        </DialogContentText>

        {done && (
          <Alert severity="success" sx={{ mb: 2 }}>
            Face enrolled for {student.name}. Running cameras load new faces automatically.
            {result?.quality?.face_size ? ` Face size ${Math.round(result.quality.face_size)} px.` : ''}
          </Alert>
        )}
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        {warning && (
          <Alert severity="warning" sx={{ mb: 2 }}
            action={<Button color="inherit" size="small" onClick={() => handleUpload(true)} disabled={uploading}>Enroll anyway</Button>}>
            {warning}
          </Alert>
        )}

        <Button variant="outlined" component="label" disabled={uploading}>
          Choose photo
          <input
            hidden
            type="file"
            accept={ALLOWED_FACE_IMAGE_TYPES.join(',')}
            onChange={handleFileChange}
            data-testid="face-file-input"
          />
        </Button>

        {file && (
          <Box sx={{ mt: 2 }}>
            <Typography variant="body2" color="text.secondary">
              {file.name} ({Math.ceil(file.size / 1024)} KB)
            </Typography>
            {previewUrl && (
              <Box
                component="img"
                src={previewUrl}
                alt={`Selected photo of ${student.name}`}
                sx={{ mt: 1, maxWidth: '100%', maxHeight: 280, borderRadius: 1, display: 'block' }}
              />
            )}
          </Box>
        )}
        {uploading && <LinearProgress sx={{ mt: 2 }} aria-label="Uploading photo" />}
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose} disabled={uploading}>{done ? 'Close' : 'Cancel'}</Button>
        <Button variant="contained" onClick={() => handleUpload(false)} disabled={!file || uploading}>
          {uploading ? 'Uploading...' : 'Upload and enroll'}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default FaceEnrollmentDialog;
