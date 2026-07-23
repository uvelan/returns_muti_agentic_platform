import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { LoadingState } from './LoadingState';
import { ErrorState } from './ErrorState';
import { PartialWarningBanner } from './PartialWarningBanner';

describe('State Components', () => {
  describe('LoadingState', () => {
    it('renders with default message', () => {
      render(<LoadingState />);
      expect(screen.getByText('Loading...')).toBeInTheDocument();
    });

    it('renders with custom message', () => {
      render(<LoadingState message="Fetching data..." />);
      expect(screen.getByText('Fetching data...')).toBeInTheDocument();
    });
  });

  describe('ErrorState', () => {
    it('renders title and message', () => {
      render(<ErrorState title="Oops" message="Something broke" />);
      expect(screen.getByText('Oops')).toBeInTheDocument();
      expect(screen.getByText('Something broke')).toBeInTheDocument();
    });
  });

  describe('PartialWarningBanner', () => {
    it('renders warnings', () => {
      render(<PartialWarningBanner message="Warning 1" />);
      expect(screen.getByText('Warning 1')).toBeInTheDocument();
    });

    it('renders default message', () => {
      render(<PartialWarningBanner />);
      expect(screen.getByText('Some data may be incomplete or missing.')).toBeInTheDocument();
    });
  });
});
