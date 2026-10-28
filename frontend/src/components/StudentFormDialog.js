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
  Switch,
  TextField
} from '@mui/material';
import { api, getErrorMessage } from '../data';

// Field limits mirror backend schemas.StudentBase
const LIMITS = { student_id: 50, name: 100, class_name: 100, department: 100 };

const EMPTY = { student_id: '', name: '', class_name: '', department: '', is_active: true, person_type: 'student' };

const toForm = (student) => (student ? {
  student_id: student.student_id || '',
  name: student.name || '',
  class_name: student.class_name || '',
  department: student.department || '',
  is_active: student.is_active !== false,
  person_type: student.person_type || 'student'
} : EMPTY);

const validate = (form) => {
  if (!form.student_id.trim()) return 'Student ID is required.';
  if (!form.name.trim()) return 'Full name is required.';
  for (const [field, max] of Object.entries(LIMITS)) {
    if (form[field].trim().length > max) return `${field.replace('_', ' ')} must be at most ${max} characters.`;
  }
  return '';
};

// Request body for POST /students/ and PUT /students/{id} (PUT replaces all fields)
const toPayload = (form) => ({
  student_id: form.student_id.trim(),
  name: form.name.trim(),
  class_name: form.class_name.trim() || null,
  department: form.department.trim() || null,
  is_active: form.is_active,
  person_type: form.person_type
});

// Add (student = null) or edit an existing student
const StudentFormDialog = ({ open, student, onClose, onSaved }) => {
  const editing = Boolean(student);
  const [form, setForm] = useState(toForm(student));
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) {
      setForm(toForm(student));
      setError('');
    }
  }, [open, student]);

  const handleChange = (e) => setForm({ ...form, [e.target.name]: e.target.value });

  const handleSubmit = async (e) => {
    e.preventDefault();
    const validationError = validate(form);
    setError(validationError);
    if (validationError) return;

    setSaving(true);
    try {
      const response = editing
        ? await api.put(`/students/${student.id}`, toPayload(form))
        : await api.post('/students/', toPayload(form));
      onSaved(response.data);
    } catch (err) {
      // e.g. "Student ID already exists", "Student ID already exists (belongs to a deleted student)"
      setError(getErrorMessage(err, editing ? 'Failed to update student' : 'Failed to add student'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={saving ? undefined : onClose} maxWidth="sm" fullWidth>
      <Box component="form" onSubmit={handleSubmit} noValidate>
        <DialogTitle>{editing ? `Edit ${student.name}` : 'Add New Student'}</DialogTitle>
        <DialogContent>
          {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
          <TextField
            label="Student ID"
            name="student_id"
            value={form.student_id}
            onChange={handleChange}
            required
            fullWidth
            margin="dense"
            disabled={editing}
            helperText={editing ? 'The student ID cannot be changed.' : 'Institution ID, must be unique.'}
            inputProps={{ maxLength: LIMITS.student_id }}
          />
          <TextField
            label="Full name"
            name="name"
            value={form.name}
            onChange={handleChange}
            required
            fullWidth
            margin="dense"
            inputProps={{ maxLength: LIMITS.name }}
          />
          <TextField
            select
            label="Person type"
            name="person_type"
            value={form.person_type}
            onChange={handleChange}
            fullWidth
            margin="dense"
            helperText="Staff are recognised and logged, but are not counted in class attendance."
          >
            <MenuItem value="student">Student</MenuItem>
            <MenuItem value="staff">Staff</MenuItem>
          </TextField>
          <TextField
            label="Class / grade"
            name="class_name"
            value={form.class_name}
            onChange={handleChange}
            fullWidth
            margin="dense"
            helperText="Class group, e.g. CSE-A. Period attendance uses students whose class matches the timetable."
            inputProps={{ maxLength: LIMITS.class_name }}
          />
          <TextField
            label="Department"
            name="department"
            value={form.department}
            onChange={handleChange}
            fullWidth
            margin="dense"
            inputProps={{ maxLength: LIMITS.department }}
          />
          <FormControlLabel
            sx={{ mt: 1 }}
            control={
              <Switch
                checked={form.is_active}
                onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
              />
            }
            label="Active (inactive students are not recognised by cameras)"
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose} disabled={saving}>Cancel</Button>
          <Button type="submit" variant="contained" disabled={saving}>
            {saving ? 'Saving...' : editing ? 'Save changes' : 'Add student'}
          </Button>
        </DialogActions>
      </Box>
    </Dialog>
  );
};

export default StudentFormDialog;
