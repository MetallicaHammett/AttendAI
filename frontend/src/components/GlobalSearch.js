import React, { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Autocomplete, Box, InputAdornment, TextField, Typography } from '@mui/material';
import SearchOutlined from '@mui/icons-material/SearchOutlined';
import { api } from '../data';

// Extra words people type for each page, so "camera", "face" or "csv" find the right one
const PAGE_KEYWORDS = {
  '/dashboard': 'home overview today summary',
  '/live-monitoring': 'live camera cameras start stop watch video feed',
  '/attendance': 'attendance today entries exits events sessions manual missed',
  '/reports': 'report reports daily monthly csv pdf export excel percentage',
  '/students': 'students staff people person add enroll enrol face photo import csv roster',
  '/cameras': 'camera cameras rtsp webcam setup add',
  '/timetable': 'timetable period periods class classes classroom room holiday schedule professor',
  '/users': 'users accounts account password role admin professor create login',
  '/staff/timetable': 'class registers professor register saved attendance'
};

export const matchPages = (pages, text) => {
  const q = text.trim().toLowerCase();
  if (!q) return pages;
  return pages.filter((p) => `${p.label} ${PAGE_KEYWORDS[p.to] || ''}`.toLowerCase().includes(q));
};

// Top-bar search: pages the user can open, and students by name or register number
const GlobalSearch = ({ pages, searchStudents }) => {
  const navigate = useNavigate();
  const inputRef = useRef(null);
  const [text, setText] = useState('');
  const [students, setStudents] = useState([]);

  // "/" jumps to the search box from anywhere (except while typing in a field)
  useEffect(() => {
    const onKey = (event) => {
      const tag = event.target?.tagName;
      if (event.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(tag) && !event.target?.isContentEditable) {
        event.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => {
    const q = text.trim();
    if (!searchStudents || q.length < 2) {
      setStudents([]);
      return undefined;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api.get('/students/', { params: { q, limit: 8 }, signal: controller.signal })
        .then((r) => setStudents(r.data.filter((s) => !s.deleted_at)))
        .catch(() => setStudents([]));
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [text, searchStudents]);

  const options = [
    ...matchPages(pages, text).map((p) => ({ kind: 'page', key: `page:${p.to}`, label: p.label, to: p.to })),
    ...students.map((s) => ({
      kind: 'person', key: `person:${s.id}`, label: `${s.name} (${s.student_id})`,
      detail: [s.person_type === 'staff' ? 'Staff' : s.class_name, s.department].filter(Boolean).join(' · '),
      to: `/students/${s.id}/attendance`
    }))
  ];

  const go = (option) => {
    if (!option) return;
    if (typeof option === 'string') {
      // Enter on free text: the Students list filtered by it
      if (searchStudents && option.trim()) navigate(`/students?q=${encodeURIComponent(option.trim())}`);
    } else {
      navigate(option.to);
    }
    setText('');
    inputRef.current?.blur();
  };

  return (
    <Autocomplete
      freeSolo
      size="small"
      options={options}
      filterOptions={(x) => x}
      groupBy={(o) => (o.kind === 'page' ? 'Pages' : 'People')}
      getOptionLabel={(o) => (typeof o === 'string' ? o : o.label)}
      isOptionEqualToValue={(a, b) => a.key === b.key}
      inputValue={text}
      onInputChange={(_, value, reason) => { if (reason !== 'reset') setText(value); }}
      value={null}
      onChange={(_, option) => go(option)}
      blurOnSelect
      clearOnBlur={false}
      sx={{ width: { xs: '100%', sm: 300, md: 360 } }}
      renderOption={(props, option) => (
        <Box component="li" {...props} key={option.key}>
          <Box>
            <Typography variant="body2">{option.label}</Typography>
            {option.detail && <Typography variant="caption" color="text.secondary">{option.detail}</Typography>}
          </Box>
        </Box>
      )}
      renderInput={(params) => (
        <TextField
          {...params}
          inputRef={inputRef}
          placeholder={searchStudents ? 'Search students or pages  ( / )' : 'Search pages  ( / )'}
          inputProps={{ ...params.inputProps, 'aria-label': 'Search' }}
          InputProps={{ ...params.InputProps,
            startAdornment: <InputAdornment position="start"><SearchOutlined fontSize="small" /></InputAdornment> }}
        />
      )}
    />
  );
};

export default GlobalSearch;
