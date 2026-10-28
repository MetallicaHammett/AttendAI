import React, { useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  MenuItem,
  Stack,
  Switch,
  TextField
} from '@mui/material';
import { api, getErrorMessage } from '../data';

// Mirrors backend schemas.CameraBase / SourceType / CameraType
export const SOURCE_TYPES = [
  { value: 'webcam', label: 'Webcam', help: 'Device index on the server, e.g. 0 for the first webcam.' },
  { value: 'video', label: 'Video file', help: 'File name inside the server\'s video folder (backend/data/videos), e.g. entrance_demo.mp4. Shown as recorded video, never as live.' },
  { value: 'rtsp', label: 'RTSP / IP stream', help: 'Stream URL, e.g. rtsp://user:password@192.168.1.20:554/stream1. Credentials are kept on the server and shown as ***; leave *** unchanged to keep them.' }
];
export const CAMERA_TYPES = [
  { value: 'IN', label: 'IN (entrance: records ENTRY)' },
  { value: 'OUT', label: 'OUT (exit: records EXIT)' }
];

const EMPTY = {
  name: '', source: '', source_type: 'webcam', camera_type: '', is_active: true,
  width: '640', height: '480', fps: '30', description: '', classroom_id: ''
};

const toForm = (camera) => (camera ? {
  name: camera.name || '',
  source: camera.source || '',
  source_type: camera.source_type || 'webcam',
  camera_type: camera.camera_type || '',
  is_active: camera.is_active !== false,
  width: String(camera.width ?? ''),
  height: String(camera.height ?? ''),
  fps: String(camera.fps ?? ''),
  description: camera.description || '',
  classroom_id: camera.classroom_id ? String(camera.classroom_id) : ''
} : EMPTY);

const isPositiveInt = (value) => /^\d+$/.test(String(value).trim()) && Number(value) > 0;

const validate = (form) => {
  if (!form.name.trim()) return 'Camera name is required.';
  if (form.name.trim().length > 100) return 'Camera name must be at most 100 characters.';
  if (!form.source.trim()) return 'Source is required.';
  if (form.source.trim().length > 255) return 'Source must be at most 255 characters.';
  if (!form.camera_type) return 'Choose whether this is an IN or OUT camera.';
  for (const field of ['width', 'height', 'fps']) {
    if (!isPositiveInt(form[field])) return `${field === 'fps' ? 'FPS' : field[0].toUpperCase() + field.slice(1)} must be a whole number greater than 0.`;
  }
  return '';
};

// Body for POST /cameras/ and PUT /cameras/{id} (PUT replaces every field)
const toPayload = (form) => ({
  name: form.name.trim(),
  source: form.source.trim(),
  source_type: form.source_type,
  camera_type: form.camera_type,
  is_active: form.is_active,
  width: Number(form.width),
  height: Number(form.height),
  fps: Number(form.fps),
  description: form.description.trim() || null,
  classroom_id: form.classroom_id ? Number(form.classroom_id) : null
});

// Add (camera = null) or edit an existing camera. `classrooms` (optional) enables the
// classroom field.
const CameraFormDialog = ({ open, camera, onClose, onSaved, classrooms = [] }) => {
  const editing = Boolean(camera);
  const [form, setForm] = useState(toForm(camera));
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) {
      setForm(toForm(camera));
      setError('');
    }
  }, [open, camera]);

  const handleChange = (e) => setForm({ ...form, [e.target.name]: e.target.value });

  const handleSubmit = async (e) => {
    e.preventDefault();
    const validationError = validate(form);
    setError(validationError);
    if (validationError) return;

    setSaving(true);
    try {
      const response = editing
        ? await api.put(`/cameras/${camera.id}`, toPayload(form))
        : await api.post('/cameras/', toPayload(form));
      onSaved(response.data);
    } catch (err) {
      setError(getErrorMessage(err, editing ? 'Failed to update camera' : 'Failed to add camera'));
    } finally {
      setSaving(false);
    }
  };

  const sourceHelp = SOURCE_TYPES.find((t) => t.value === form.source_type)?.help;

  return (
    <Dialog open={open} onClose={saving ? undefined : onClose} maxWidth="sm" fullWidth>
      <Box component="form" onSubmit={handleSubmit} noValidate>
        <DialogTitle>{editing ? `Edit ${camera.name}` : 'Add New Camera'}</DialogTitle>
        <DialogContent>
          {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
          <TextField
            label="Camera name"
            name="name"
            value={form.name}
            onChange={handleChange}
            required
            fullWidth
            margin="dense"
            inputProps={{ maxLength: 100 }}
          />
          <Stack direction={{ xs: 'column', sm: 'row' }} gap={2}>
            <TextField
              select
              label="Camera type"
              name="camera_type"
              value={form.camera_type}
              onChange={handleChange}
              required
              fullWidth
              margin="dense"
              helperText="IN cameras record entries, OUT cameras record exits."
            >
              {CAMERA_TYPES.map((t) => <MenuItem key={t.value} value={t.value}>{t.label}</MenuItem>)}
            </TextField>
            <TextField
              select
              label="Source type"
              name="source_type"
              value={form.source_type}
              onChange={handleChange}
              required
              fullWidth
              margin="dense"
            >
              {SOURCE_TYPES.map((t) => <MenuItem key={t.value} value={t.value}>{t.label}</MenuItem>)}
            </TextField>
          </Stack>
          <TextField
            label="Source"
            name="source"
            value={form.source}
            onChange={handleChange}
            required
            fullWidth
            margin="dense"
            helperText={sourceHelp}
            inputProps={{ maxLength: 255 }}
          />
          <Stack direction="row" gap={2}>
            <TextField label="Width (px)" name="width" value={form.width} onChange={handleChange} margin="dense"
              required inputProps={{ inputMode: 'numeric' }} />
            <TextField label="Height (px)" name="height" value={form.height} onChange={handleChange} margin="dense"
              required inputProps={{ inputMode: 'numeric' }} />
            <TextField label="FPS" name="fps" value={form.fps} onChange={handleChange} margin="dense"
              required inputProps={{ inputMode: 'numeric' }} />
          </Stack>
          {classrooms.length > 0 && (
            <TextField
              select
              label="Classroom"
              name="classroom_id"
              value={form.classroom_id}
              onChange={handleChange}
              fullWidth
              margin="dense"
              helperText="Period attendance uses the IN cameras of the period's classroom to decide whether anyone was watching."
            >
              <MenuItem value="">Not assigned</MenuItem>
              {classrooms.map((room) => <MenuItem key={room.id} value={String(room.id)}>{room.name}</MenuItem>)}
            </TextField>
          )}
          <TextField
            label="Description"
            name="description"
            value={form.description}
            onChange={handleChange}
            fullWidth
            multiline
            minRows={2}
            margin="dense"
          />
          <FormControlLabel
            sx={{ mt: 1 }}
            control={<Switch checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} />}
            label="Active (only active cameras can start processing)"
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose} disabled={saving}>Cancel</Button>
          <Button type="submit" variant="contained" disabled={saving}>
            {saving ? 'Saving...' : editing ? 'Save changes' : 'Add camera'}
          </Button>
        </DialogActions>
      </Box>
    </Dialog>
  );
};

export default CameraFormDialog;
