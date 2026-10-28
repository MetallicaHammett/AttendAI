import React, { useCallback, useContext, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Paper,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography
} from '@mui/material';
import { ConfirmDialog, EmptyState, ErrorState, LoadingState, PageHeader, SectionCard, formatDateTime } from '../components/ui';
import { ACCOUNT_LABELS, AuthContext, ROLE_LABELS, usePermission } from '../context/AuthContext';
import { useNotify } from '../context/NotifyContext';
import { api, getErrorMessage } from '../data';

const ROLE_HELP = {
  viewer: 'Read-only: dashboards, attendance and reports',
  operator: 'Day-to-day: people, enrollment, cameras on/off, corrections',
  admin: 'Everything, including cameras, timetable and accounts',
  professor: 'Staff portal only: their own timetable and class registers (assign periods on the Timetable page)',
  student: 'Only their own attendance page; no staff pages'
};

const studentLabel = (s) => `${s.name} (${s.student_id})`;

// Same rules as the backend (schemas.UserCreate); the server checks again
const USERNAME_RE = /^[A-Za-z0-9._-]{3,50}$/;
const EMAIL_RE = /^[^@\s[\]()<>,;"']+@[^@\s[\]()<>,;"']+\.[^@\s[\]()<>,;"'.]+$/;
const EMPTY_ACCOUNT = { username: '', email: '', role: 'viewer', student: null, password: '', confirm: '' };

// bcrypt uses at most 72 bytes, so the limit is in UTF-8 bytes, not characters
const utf8Length = (text) => Array.from(text).reduce((n, ch) => {
  const code = ch.codePointAt(0);
  return n + (code < 0x80 ? 1 : code < 0x800 ? 2 : code < 0x10000 ? 3 : 4);
}, 0);

export const validateAccount = (form) => {
  if (!USERNAME_RE.test(form.username.trim())) return "Username must be 3-50 characters: letters, digits, '.', '_' or '-'.";
  if (!EMAIL_RE.test(form.email.trim())) return 'Enter a plain email address such as name@college.edu.';
  if (form.role === 'student' && !form.student) return 'Choose the student this account belongs to.';
  const bytes = utf8Length(form.password);
  if (bytes < 8 || bytes > 72) return 'The initial password must be 8-72 characters.';
  if (form.password !== form.confirm) return 'The passwords do not match.';
  return '';
};

const CreateAccountDialog = ({ open, onClose, onCreated, students, linkedStudentIds }) => {
  const [form, setForm] = useState(EMPTY_ACCOUNT);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  // The form, including both password fields, is cleared every time the dialog opens
  useEffect(() => { if (open) { setForm(EMPTY_ACCOUNT); setError(''); } }, [open]);

  const save = async () => {
    const problem = validateAccount(form);
    if (problem) {
      setError(problem);
      return;
    }
    setSaving(true);
    setError('');
    try {
      const body = { username: form.username.trim(), email: form.email.trim(), role: form.role, password: form.password };
      if (form.role === 'student') body.student_db_id = form.student.id;
      const response = await api.post('/users/', body);
      setForm(EMPTY_ACCOUNT);
      onCreated(response.data);
    } catch (err) {
      setError(getErrorMessage(err, 'Could not create the account'));
    } finally {
      setSaving(false);
    }
  };

  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });
  return (
    <Dialog open={open} onClose={saving ? undefined : onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Create account</DialogTitle>
      <DialogContent>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        <Stack direction={{ xs: 'column', sm: 'row' }} gap={2}>
          <TextField label="Username" value={form.username} onChange={set('username')} margin="dense" fullWidth
            autoComplete="off" helperText="Letters, digits, '.', '_' or '-'" />
          <TextField label="Email" type="email" value={form.email} onChange={set('email')} margin="dense" fullWidth autoComplete="off" />
        </Stack>
        <TextField select label="Role" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value, student: null })}
          margin="dense" fullWidth helperText={ROLE_HELP[form.role]}>
          {Object.entries(ACCOUNT_LABELS).map(([value, label]) => <MenuItem key={value} value={value}>{label}</MenuItem>)}
        </TextField>
        {form.role === 'student' && (
          <Autocomplete
            options={students}
            value={form.student}
            onChange={(_, value) => setForm((f) => ({ ...f, student: value }))}
            getOptionLabel={studentLabel}
            getOptionDisabled={(s) => linkedStudentIds.has(s.id)}
            isOptionEqualToValue={(a, b) => a.id === b.id}
            noOptionsText="No current students"
            renderOption={(props, s) => (
              <li {...props} key={s.id}>{studentLabel(s)}{linkedStudentIds.has(s.id) ? ' · already has an account' : ''}</li>
            )}
            renderInput={(params) => (
              <TextField {...params} label="Student record" margin="dense"
                helperText="The account sees only this student's attendance" />
            )}
          />
        )}
        {form.role === 'admin' && (
          <Alert severity="warning" sx={{ my: 1 }}>
            Administrators can manage cameras, the timetable and every account. Grant this only to trusted staff.
          </Alert>
        )}
        <Stack direction={{ xs: 'column', sm: 'row' }} gap={2}>
          <TextField label="Initial password" type="password" value={form.password} onChange={set('password')} margin="dense"
            fullWidth autoComplete="new-password" helperText="8-72 characters" />
          <TextField label="Repeat password" type="password" value={form.confirm} onChange={set('confirm')} margin="dense"
            fullWidth autoComplete="new-password" />
        </Stack>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
          Give the password to the person directly, not by email or chat. The creation is recorded in the audit log; the password is not.
        </Typography>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={saving}>Cancel</Button>
        <Button variant="contained" onClick={save} disabled={saving}>
          {saving ? 'Creating…' : form.role === 'admin' ? 'Create administrator' : 'Create account'}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

const UsersPage = () => {
  const isAdmin = usePermission('admin');
  const auth = useContext(AuthContext) || {};
  const notify = useNotify();
  const [users, setUsers] = useState([]);
  const [audit, setAudit] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [savingId, setSavingId] = useState(null);
  // Role and access changes to real accounts are confirmed before they are sent
  const [pending, setPending] = useState(null); // { user, changes }
  const [creating, setCreating] = useState(false);
  // ?new=1 (home shortcut) opens "Create account" straight away
  const [searchParams, setSearchParams] = useSearchParams();
  useEffect(() => {
    if (searchParams.get('new') !== '1') return;
    if (isAdmin) setCreating(true);
    setSearchParams({}, { replace: true });
  }, [searchParams, setSearchParams, isAdmin]);
  // Current students, for linking student accounts and naming them in the list
  const [students, setStudents] = useState([]);

  const loadStudents = useCallback(() => {
    api.get('/students/', { params: { limit: 5000 } })
      .then((r) => setStudents(r.data.filter((s) => s.person_type !== 'staff' && !s.deleted_at)))
      .catch(() => {}); // only labels and the picker depend on it; the page works without
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [usersResponse, auditResponse] = await Promise.all([
        api.get('/users/'), api.get('/users/audit-log', { params: { limit: 100 } })
      ]);
      setUsers(usersResponse.data);
      setAudit(auditResponse.data);
    } catch (err) {
      setError(getErrorMessage(err, 'Failed to load accounts'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { if (isAdmin) { load(); loadStudents(); } }, [isAdmin, load, loadStudents]);

  const linkedStudentIds = new Set(users.map((u) => u.student_db_id).filter(Boolean));
  const studentById = new Map(students.map((s) => [s.id, s]));

  const describe = ({ user, changes }) => {
    if (changes.role) {
      return `Change ${user.username} from ${ACCOUNT_LABELS[user.role] || user.role} to ${ROLE_LABELS[changes.role]}? `
        + `${ROLE_HELP[changes.role]}. The change applies to their next request.`;
    }
    return changes.is_active
      ? `Reactivate ${user.username}? They will be able to sign in again.`
      : `Deactivate ${user.username}? They are signed out at their next request and cannot sign in. Their records are kept.`;
  };

  const confirmPending = async () => {
    const { user, changes } = pending;
    setPending(null);
    await update(user, changes);
  };

  const update = async (user, changes) => {
    setSavingId(user.id);
    try {
      const response = await api.patch(`/users/${user.id}`, changes);
      setUsers((current) => current.map((u) => (u.id === user.id ? response.data : u)));
      notify(`Updated ${user.username}`);
      api.get('/users/audit-log', { params: { limit: 100 } }).then((r) => setAudit(r.data)).catch(() => {});
    } catch (err) {
      notify(getErrorMessage(err, `Could not update ${user.username}`), 'error');
    } finally {
      setSavingId(null);
    }
  };

  if (!isAdmin) {
    return (
      <Box>
        <PageHeader title="Users" />
        <Alert severity="info">Only administrators can manage accounts.</Alert>
      </Box>
    );
  }

  return (
    <Box>
      <PageHeader title="Users" subtitle="Accounts, roles and the audit trail of privileged changes. New sign-ups are read-only viewers."
        actions={<Button variant="contained" onClick={() => setCreating(true)}>Create account</Button>} />
      <CreateAccountDialog
        open={creating}
        students={students}
        linkedStudentIds={linkedStudentIds}
        onClose={() => setCreating(false)}
        onCreated={(user) => {
          setCreating(false);
          setUsers((current) => [...current, user]);
          notify(`Created ${user.username} (${ACCOUNT_LABELS[user.role] || user.role})`);
          api.get('/users/audit-log', { params: { limit: 100 } }).then((r) => setAudit(r.data)).catch(() => {});
        }}
      />
      {error && <Box sx={{ mb: 2 }}><ErrorState message={error} onRetry={load} /></Box>}
      {loading && users.length === 0 ? <LoadingState label="Loading accounts..." /> : (
        <TableContainer component={Paper} variant="outlined" sx={{ mb: 3 }}>
          <Table size="small" aria-label="Accounts">
            <TableHead>
              <TableRow>
                <TableCell>Username</TableCell><TableCell>Email</TableCell><TableCell>Role</TableCell>
                <TableCell>Active</TableCell><TableCell>Created</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {users.map((user) => {
                const self = user.id === auth.user?.id;
                return (
                  <TableRow key={user.id} hover>
                    <TableCell>{user.username}{self && <Chip size="small" label="you" sx={{ ml: 1 }} />}</TableCell>
                    <TableCell>{user.email}</TableCell>
                    <TableCell>
                      {user.role === 'student' ? (
                        // A student account stays tied to its student; it is never given a staff role
                        <Chip size="small" color="primary" variant="outlined"
                          label={`Student · ${studentById.has(user.student_db_id) ? studentLabel(studentById.get(user.student_db_id)) : `record #${user.student_db_id}`}`} />
                      ) : (
                      <TextField select size="small" value={user.role} disabled={self || savingId === user.id}
                        onChange={(e) => { if (e.target.value !== user.role) setPending({ user, changes: { role: e.target.value } }); }}
                        inputProps={{ 'aria-label': `Role of ${user.username}` }}
                        helperText={ROLE_HELP[user.role]} sx={{ minWidth: 170 }}>
                        {Object.entries(ROLE_LABELS).map(([value, label]) => <MenuItem key={value} value={value}>{label}</MenuItem>)}
                      </TextField>
                      )}
                    </TableCell>
                    <TableCell>
                      <Switch checked={user.is_active} disabled={self || savingId === user.id}
                        onChange={(e) => setPending({ user, changes: { is_active: e.target.checked } })}
                        inputProps={{ 'aria-label': `${user.is_active ? 'Deactivate' : 'Activate'} ${user.username}` }} />
                    </TableCell>
                    <TableCell>{formatDateTime(user.created_at)}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </TableContainer>
      )}
      <ConfirmDialog
        open={Boolean(pending)}
        title={pending?.changes.role ? 'Change role?' : pending?.changes.is_active ? 'Reactivate account?' : 'Deactivate account?'}
        confirmLabel={pending?.changes.role ? 'Change role' : pending?.changes.is_active ? 'Reactivate' : 'Deactivate'}
        danger={pending?.changes.is_active === false}
        onCancel={() => setPending(null)}
        onConfirm={confirmPending}
      >
        {pending ? describe(pending) : ''}
      </ConfirmDialog>
      <SectionCard title="Audit log" subtitle="Latest 100 privileged changes (no passwords, credentials or face data are recorded)">
        {audit.length === 0 ? <EmptyState title="No entries yet" /> : (
          <TableContainer>
            <Table size="small" aria-label="Audit log">
              <TableHead>
                <TableRow><TableCell>When</TableCell><TableCell>Who</TableCell><TableCell>Action</TableCell><TableCell>Details</TableCell></TableRow>
              </TableHead>
              <TableBody>
                {audit.map((entry) => (
                  <TableRow key={entry.id}>
                    <TableCell sx={{ whiteSpace: 'nowrap' }}>{formatDateTime(entry.created_at)}</TableCell>
                    <TableCell>{entry.username || '—'}</TableCell>
                    <TableCell><Typography variant="body2" sx={{ fontFamily: 'monospace' }}>{entry.action}</Typography></TableCell>
                    <TableCell sx={{ maxWidth: 480 }}>
                      <Typography variant="caption" sx={{ wordBreak: 'break-word' }}>
                        {entry.entity_type}{entry.entity_id ? ` #${entry.entity_id}` : ''}{entry.details ? ` · ${JSON.stringify(entry.details)}` : ''}
                      </Typography>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </SectionCard>
    </Box>
  );
};

export default UsersPage;
