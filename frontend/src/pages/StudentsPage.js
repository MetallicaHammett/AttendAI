import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link as RouterLink, useSearchParams } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  FormControlLabel,
  MenuItem,
  Paper,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TablePagination,
  TableRow,
  TableSortLabel,
  TextField,
  Typography
} from '@mui/material';
import StudentFormDialog from '../components/StudentFormDialog';
import FaceEnrollmentDialog from '../components/FaceEnrollmentDialog';
import RosterImportDialog from '../components/RosterImportDialog';
import PersonDetailDrawer from '../components/PersonDetailDrawer';
import { PageHeader } from '../components/ui';
import { usePermission } from '../context/AuthContext';
import { useNotify } from '../context/NotifyContext';
import { api, getErrorMessage } from '../data';

export const PAGE_SIZE_OPTIONS = [25, 50, 100];

const SORTERS = {
  name: (a, b) => a.name.localeCompare(b.name),
  student_id: (a, b) => a.student_id.localeCompare(b.student_id, undefined, { numeric: true }),
  class_name: (a, b) => (a.class_name || '').localeCompare(b.class_name || '')
};

const EnrollmentChip = ({ student }) => {
  if (student.enrolled_faces === undefined) return null;
  return student.enrolled_faces > 0
    ? <Chip size="small" variant="outlined" color="success" label={`${student.enrolled_faces} face${student.enrolled_faces === 1 ? '' : 's'}`} />
    : <Chip size="small" variant="outlined" color="warning" label="Not enrolled" />;
};

// GET /students/ defaults to 100 rows; ask for more so search covers everyone
export const STUDENT_LIST_LIMIT = 1000;

const matches = (student, query) => {
  if (!query) return true;
  const q = query.toLowerCase();
  return [student.student_id, student.name, student.class_name, student.department]
    .some((value) => (value || '').toLowerCase().includes(q));
};

const StatusChip = ({ student }) => {
  if (student.deleted_at) {
    return <Chip size="small" label={`Deleted ${new Date(student.deleted_at).toLocaleDateString()}`} />;
  }
  return student.is_active
    ? <Chip size="small" color="success" label="Active" />
    : <Chip size="small" color="warning" label="Inactive" />;
};

const StudentsPage = () => {
  // ?q= pre-fills the search (top-bar search); ?new=1 opens "Add student" (home shortcut)
  const [searchParams, setSearchParams] = useSearchParams();
  const [students, setStudents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [search, setSearch] = useState(() => searchParams.get('q') || '');
  const [showDeleted, setShowDeleted] = useState(false);
  const [notice, setNotice] = useState(null); // { severity, text }

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [deleting, setDeleting] = useState(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState('');
  const [enrolling, setEnrolling] = useState(null);
  const [importOpen, setImportOpen] = useState(false);
  const [detail, setDetail] = useState(null);
  const [typeFilter, setTypeFilter] = useState('all');
  const [enrollmentFilter, setEnrollmentFilter] = useState('all');
  const [sort, setSort] = useState({ by: 'student_id', direction: 'asc' });
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(PAGE_SIZE_OPTIONS[1]);
  const canOperate = usePermission('operator');
  const canDelete = usePermission('admin');
  const notify = useNotify();

  const loadStudents = useCallback(async (signal) => {
    setLoading(true);
    setLoadError('');
    try {
      const response = await api.get('/students/', {
        params: { limit: STUDENT_LIST_LIMIT, include_deleted: showDeleted },
        signal
      });
      setStudents(response.data);
    } catch (err) {
      if (signal?.aborted) return;
      setLoadError(getErrorMessage(err, 'Failed to load students'));
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [showDeleted]);

  useEffect(() => {
    const controller = new AbortController();
    loadStudents(controller.signal);
    return () => controller.abort();
  }, [loadStudents]);

  const filtered = useMemo(() => {
    const rows = students.filter((s) => matches(s, search.trim())
      && (typeFilter === 'all' || (s.person_type || 'student') === typeFilter)
      && (enrollmentFilter === 'all' || (enrollmentFilter === 'enrolled') === ((s.enrolled_faces ?? 0) > 0)));
    const sorter = SORTERS[sort.by];
    return sorter ? [...rows].sort((a, b) => (sort.direction === 'asc' ? 1 : -1) * sorter(a, b)) : rows;
  }, [students, search, typeFilter, enrollmentFilter, sort]);
  const visible = useMemo(() => filtered.slice(page * pageSize, (page + 1) * pageSize), [filtered, page, pageSize]);
  useEffect(() => { setPage(0); }, [search, typeFilter, enrollmentFilter, showDeleted]);
  const notEnrolled = students.filter((s) => !s.deleted_at && s.is_active && s.enrolled_faces === 0).length;

  const sortBy = (by) => setSort((current) => ({ by, direction: current.by === by && current.direction === 'asc' ? 'desc' : 'asc' }));

  const openAdd = () => { setEditing(null); setFormOpen(true); };

  // Shortcut links: follow them once, then drop them from the address so a reload doesn't repeat them
  useEffect(() => {
    const q = searchParams.get('q');
    const wantsNew = searchParams.get('new') === '1';
    if (q !== null) setSearch(q);
    if (wantsNew && canOperate) { setEditing(null); setFormOpen(true); }
    if (q !== null || wantsNew) setSearchParams({}, { replace: true });
  }, [searchParams, setSearchParams, canOperate]);

  const openEdit = (student) => { setEditing(student); setFormOpen(true); };

  const handleSaved = (saved) => {
    setStudents((current) => {
      const exists = current.some((s) => s.id === saved.id);
      return exists ? current.map((s) => (s.id === saved.id ? saved : s)) : [...current, saved];
    });
    setNotice({ severity: 'success', text: editing ? `Saved changes to ${saved.name}.` : `Added ${saved.name}.` });
    setFormOpen(false);
    setEditing(null);
  };

  const openDelete = (student) => { setDeleteError(''); setDeleting(student); };

  const confirmDelete = async () => {
    setDeleteBusy(true);
    setDeleteError('');
    try {
      await api.delete(`/students/${deleting.id}`);
      setNotice({ severity: 'success', text: `Deleted ${deleting.name}. Their attendance history is kept.` });
      setDeleting(null);
      await loadStudents();
    } catch (err) {
      setDeleteError(getErrorMessage(err, 'Failed to delete student'));
    } finally {
      setDeleteBusy(false);
    }
  };

  return (
    <Box>
      <PageHeader
        title="Students"
        subtitle="Students and staff who can be recognised. Only enrolled people (with a face record) are identified by cameras."
        actions={canOperate && (
          <>
            <Button variant="outlined" onClick={() => setImportOpen(true)}>Import CSV</Button>
            <Button variant="contained" onClick={openAdd}>Add student</Button>
          </>
        )}
      />

      <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" alignItems="center" flexWrap="wrap" gap={2}>
          <TextField
            label="Search students"
            placeholder="Name, ID, class or department"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            size="small"
            sx={{ width: 320, maxWidth: '100%' }}
          />
          <TextField select size="small" label="Type" value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} sx={{ minWidth: 140 }}>
            <MenuItem value="all">Students and staff</MenuItem>
            <MenuItem value="student">Students</MenuItem>
            <MenuItem value="staff">Staff</MenuItem>
          </TextField>
          <TextField select size="small" label="Enrollment" value={enrollmentFilter} onChange={(e) => setEnrollmentFilter(e.target.value)} sx={{ minWidth: 160 }}>
            <MenuItem value="all">Any</MenuItem>
            <MenuItem value="enrolled">Enrolled</MenuItem>
            <MenuItem value="not_enrolled">Not enrolled</MenuItem>
          </TextField>
          <FormControlLabel
            control={<Switch checked={showDeleted} onChange={(e) => setShowDeleted(e.target.checked)} />}
            label="Show deleted"
          />
        </Stack>
      </Paper>

      {notice && (
        <Alert severity={notice.severity} onClose={() => setNotice(null)} sx={{ mb: 2 }}>{notice.text}</Alert>
      )}
      {notEnrolled > 0 && enrollmentFilter === 'all' && (
        <Alert severity="info" sx={{ mb: 2 }}
          action={<Button color="inherit" size="small" onClick={() => setEnrollmentFilter('not_enrolled')}>Show them</Button>}>
          {notEnrolled} active {notEnrolled === 1 ? 'person is' : 'people are'} not enrolled and cannot be recognised yet.
        </Alert>
      )}

      {loading && students.length === 0 ? (
        <Box sx={{ textAlign: 'center', py: 4 }}>
          <CircularProgress />
          <Typography sx={{ mt: 2 }} variant="body2">Loading students...</Typography>
        </Box>
      ) : loadError ? (
        <Alert
          severity="error"
          action={<Button color="inherit" size="small" onClick={() => loadStudents()}>Retry</Button>}
        >
          {loadError}
        </Alert>
      ) : students.length === 0 ? (
        <Typography variant="body2" sx={{ textAlign: 'center', py: 4 }}>
          No students yet. Use “Add student” to create one.
        </Typography>
      ) : filtered.length === 0 ? (
        <Typography variant="body2" sx={{ textAlign: 'center', py: 4 }}>
          {search.trim() ? `No students match “${search.trim()}”.` : 'No people match the selected filters.'}
        </Typography>
      ) : (
        <Paper variant="outlined">
        <TableContainer>
          <Table size="small" aria-label="Students">
            <TableHead>
              <TableRow>
                <TableCell sortDirection={sort.by === 'student_id' ? sort.direction : false}>
                  <TableSortLabel active={sort.by === 'student_id'} direction={sort.direction} onClick={() => sortBy('student_id')}>Student ID</TableSortLabel>
                </TableCell>
                <TableCell sortDirection={sort.by === 'name' ? sort.direction : false}>
                  <TableSortLabel active={sort.by === 'name'} direction={sort.direction} onClick={() => sortBy('name')}>Name</TableSortLabel>
                </TableCell>
                <TableCell sortDirection={sort.by === 'class_name' ? sort.direction : false}>
                  <TableSortLabel active={sort.by === 'class_name'} direction={sort.direction} onClick={() => sortBy('class_name')}>Class</TableSortLabel>
                </TableCell>
                <TableCell>Department</TableCell>
                <TableCell>Face</TableCell>
                <TableCell>Status</TableCell>
                <TableCell align="right">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {visible.map((student) => {
                const deleted = Boolean(student.deleted_at);
                return (
                  <TableRow key={student.id} hover sx={deleted ? { opacity: 0.6 } : undefined}>
                    <TableCell component="th" scope="row">{student.student_id}</TableCell>
                    <TableCell>
                      {student.name}
                      {student.person_type === 'staff' && <Chip size="small" label="Staff" sx={{ ml: 1 }} />}
                    </TableCell>
                    <TableCell>{student.class_name || '-'}</TableCell>
                    <TableCell>{student.department || '-'}</TableCell>
                    <TableCell><EnrollmentChip student={student} /></TableCell>
                    <TableCell><StatusChip student={student} /></TableCell>
                    <TableCell align="right">
                      {deleted ? (
                        <Typography variant="caption" color="text.secondary">Read only</Typography>
                      ) : (
                        <Stack direction="row" gap={1} justifyContent="flex-end">
                          <Button size="small" onClick={() => setDetail(student)} aria-label={`Details for ${student.name}`}>
                            Details
                          </Button>
                          {student.person_type !== 'staff' && (
                            <Button size="small" component={RouterLink} to={`/students/${student.id}/attendance`}
                              aria-label={`Attendance of ${student.name}`}>
                              Attendance
                            </Button>
                          )}
                          {canOperate && (
                            <>
                              <Button
                                size="small"
                                onClick={() => setEnrolling(student)}
                                disabled={!student.is_active}
                                aria-label={`Enroll face for ${student.name}`}
                                title={student.is_active ? undefined : 'Activate the student before enrolling a face'}
                              >
                                Enroll face
                              </Button>
                              <Button size="small" onClick={() => openEdit(student)} aria-label={`Edit ${student.name}`}>
                                Edit
                              </Button>
                            </>
                          )}
                          {canDelete && (
                            <Button size="small" color="error" onClick={() => openDelete(student)} aria-label={`Delete ${student.name}`}>
                              Delete
                            </Button>
                          )}
                        </Stack>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </TableContainer>
        <TablePagination
          component="div"
          count={filtered.length}
          page={page}
          onPageChange={(_, next) => setPage(next)}
          rowsPerPage={pageSize}
          rowsPerPageOptions={PAGE_SIZE_OPTIONS}
          onRowsPerPageChange={(e) => { setPageSize(Number(e.target.value)); setPage(0); }}
        />
        </Paper>
      )}

      <RosterImportDialog
        open={importOpen}
        onClose={() => setImportOpen(false)}
        onImported={(count) => {
          setImportOpen(false);
          notify(`Imported ${count} ${count === 1 ? 'person' : 'people'}.`);
          setNotice({ severity: 'success', text: `Imported ${count} ${count === 1 ? 'person' : 'people'} from the roster file.` });
          loadStudents();
        }}
      />
      <PersonDetailDrawer
        person={detail}
        onClose={() => setDetail(null)}
        onEnroll={(person) => setEnrolling(person)}
        onChanged={() => loadStudents()}
      />

      <StudentFormDialog
        open={formOpen}
        student={editing}
        onClose={() => { setFormOpen(false); setEditing(null); }}
        onSaved={handleSaved}
      />

      <Dialog open={Boolean(deleting)} onClose={deleteBusy ? undefined : () => setDeleting(null)}>
        <DialogTitle>Delete student?</DialogTitle>
        <DialogContent>
          {deleteError && <Alert severity="error" sx={{ mb: 2 }}>{deleteError}</Alert>}
          <DialogContentText>
            Delete {deleting?.name} ({deleting?.student_id})? They will be hidden from the student list and
            no longer recognised by cameras. Their attendance history is kept. The student ID cannot be
            reused, and the app cannot undo this.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleting(null)} disabled={deleteBusy}>Cancel</Button>
          <Button color="error" variant="contained" onClick={confirmDelete} disabled={deleteBusy}>
            {deleteBusy ? 'Deleting...' : 'Delete student'}
          </Button>
        </DialogActions>
      </Dialog>

      <FaceEnrollmentDialog
        open={Boolean(enrolling)}
        student={enrolling}
        onClose={() => setEnrolling(null)}
        onEnrolled={(student) => {
          setNotice({ severity: 'success', text: `Face enrolled for ${student.name}.` });
          setStudents((current) => current.map((s) => (s.id === student.id && s.enrolled_faces !== undefined
            ? { ...s, enrolled_faces: s.enrolled_faces + 1 } : s)));
        }}
      />
    </Box>
  );
};

export default StudentsPage;
