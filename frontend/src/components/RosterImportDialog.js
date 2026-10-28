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
  Link,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography
} from '@mui/material';
import { api, downloadFile, getBlobErrorMessage, getErrorMessage } from '../data';

export const MAX_ROSTER_BYTES = 1024 * 1024;

// Two steps: validate (dry run, nothing is written) -> import (all rows or none)
const RosterImportDialog = ({ open, onClose, onImported }) => {
  const [file, setFile] = useState(null);
  const [report, setReport] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open) {
      setFile(null);
      setReport(null);
      setError('');
    }
  }, [open]);

  const send = async (selected, dryRun) => {
    const body = new FormData();
    body.append('file', selected);
    const response = await api.post('/students/import', body, { params: { dry_run: dryRun } });
    return response.data;
  };

  const handleFile = async (e) => {
    const selected = e.target.files && e.target.files[0];
    e.target.value = '';
    setReport(null);
    setError('');
    if (!selected) return;
    if (!selected.name.toLowerCase().endsWith('.csv')) {
      setError('Choose a .csv file (use the template).');
      return;
    }
    if (selected.size > MAX_ROSTER_BYTES) {
      setError('The file is larger than 1 MB.');
      return;
    }
    setFile(selected);
    setBusy(true);
    try {
      setReport(await send(selected, true));
    } catch (err) {
      setError(getErrorMessage(err, 'Could not validate the file'));
    } finally {
      setBusy(false);
    }
  };

  const handleImport = async () => {
    setBusy(true);
    setError('');
    try {
      const result = await send(file, false);
      if (result.errors.length > 0) {
        setReport(result); // data changed between validation and import
      } else {
        onImported(result.created);
      }
    } catch (err) {
      setError(getErrorMessage(err, 'Import failed'));
    } finally {
      setBusy(false);
    }
  };

  const downloadTemplate = async () => {
    try {
      await downloadFile('/students/import/template', undefined, 'attendai_roster_template.csv');
    } catch (err) {
      setError(await getBlobErrorMessage(err, 'Could not download the template'));
    }
  };

  const valid = report && report.errors.length === 0 && report.valid_rows > 0;

  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} maxWidth="md" fullWidth>
      <DialogTitle>Import class roster</DialogTitle>
      <DialogContent>
        <DialogContentText sx={{ mb: 2 }}>
          Upload a CSV with the columns student_id, name, class_name, department, person_type (student or staff) and
          is_active. The file is checked first; nothing is saved until every row is valid.{' '}
          <Link component="button" type="button" onClick={downloadTemplate}>Download the template</Link>.
        </DialogContentText>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        <Button variant="outlined" component="label" disabled={busy}>
          Choose CSV file
          <input hidden type="file" accept=".csv,text/csv" onChange={handleFile} data-testid="roster-file-input" />
        </Button>
        {file && <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>{file.name}</Typography>}
        {busy && <LinearProgress sx={{ mt: 2 }} aria-label="Checking file" />}

        {report && (
          <Box sx={{ mt: 2 }}>
            {valid ? (
              <Alert severity="success">All {report.valid_rows} rows are valid and ready to import.</Alert>
            ) : (
              <Alert severity="error" sx={{ mb: 1 }}>
                {report.errors.length} problem{report.errors.length === 1 ? '' : 's'} found in {report.total_rows} row
                {report.total_rows === 1 ? '' : 's'}. Fix the file and choose it again; nothing was imported.
              </Alert>
            )}
            {report.errors.length > 0 && (
              <TableContainer sx={{ maxHeight: 280 }}>
                <Table size="small" stickyHeader aria-label="Import problems">
                  <TableHead>
                    <TableRow><TableCell>Line</TableCell><TableCell>Column</TableCell><TableCell>Problem</TableCell></TableRow>
                  </TableHead>
                  <TableBody>
                    {report.errors.map((e, i) => (
                      <TableRow key={`${e.row}-${e.field}-${i}`}>
                        <TableCell>{e.row || '—'}</TableCell>
                        <TableCell>{e.field}</TableCell>
                        <TableCell>{e.message}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            )}
          </Box>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="contained" onClick={handleImport} disabled={!valid || busy}>
          {valid ? `Import ${report.valid_rows} people` : 'Import'}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default RosterImportDialog;
