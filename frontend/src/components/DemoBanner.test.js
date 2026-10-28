import React from 'react';
import { render, screen } from '@testing-library/react';
import { DemoBanner, isDemoMode } from './ui';

const previous = process.env.REACT_APP_DEMO_MODE;
afterEach(() => {
  if (previous === undefined) delete process.env.REACT_APP_DEMO_MODE; else process.env.REACT_APP_DEMO_MODE = previous;
});

test('the real app shows no demo banner', () => {
  delete process.env.REACT_APP_DEMO_MODE;
  render(<DemoBanner />);
  expect(isDemoMode()).toBe(false);
  expect(screen.queryByRole('note')).not.toBeInTheDocument();
});

test('the demo instance labels every page as fictional, synthetic data', () => {
  process.env.REACT_APP_DEMO_MODE = 'true';
  render(<DemoBanner />);
  expect(screen.getByRole('note')).toHaveTextContent(/DEMO instance.*fictional.*not from cameras.*Not the real AttendAI database/);
});
