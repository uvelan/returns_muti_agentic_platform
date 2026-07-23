import { describe, it, expect, vi, beforeAll } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ConfirmationDialog } from './ConfirmationDialog';

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = vi.fn();
  HTMLDialogElement.prototype.close = vi.fn();
});

describe('ConfirmationDialog', () => {
  it('renders content correctly', () => {
    render(
      <ConfirmationDialog
        isOpen={true}
        title="Test Dialog"
        description="Are you sure?"
        confirmText="Yes"
        cancelText="No"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />
    );
    expect(screen.getByText('Test Dialog')).toBeInTheDocument();
    expect(screen.getByText('Are you sure?')).toBeInTheDocument();
    expect(screen.getByText('Yes')).toBeInTheDocument();
    expect(screen.getByText('No')).toBeInTheDocument();
  });

  it('calls onConfirm when confirm button is clicked', () => {
    let confirmed = false;
    render(
      <ConfirmationDialog
        isOpen={true}
        title="Test Dialog"
        description="Are you sure?"
        onConfirm={() => { confirmed = true; }}
        onCancel={vi.fn()}
      />
    );
    fireEvent.click(screen.getByText('Confirm'));
    expect(confirmed).toBe(true);
  });

  it('calls onCancel when cancel button is clicked', () => {
    let canceled = false;
    render(
      <ConfirmationDialog
        isOpen={true}
        title="Test Dialog"
        description="Are you sure?"
        onConfirm={vi.fn()}
        onCancel={() => { canceled = true; }}
      />
    );
    fireEvent.click(screen.getByText('Cancel'));
    expect(canceled).toBe(true);
  });
});
