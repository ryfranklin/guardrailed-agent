interface ErrorBannerProps {
  message: string;
  onDismiss: () => void;
}

export function ErrorBanner({ message, onDismiss }: ErrorBannerProps) {
  return (
    <div
      className="flex items-start justify-between gap-3 border-b border-rose-200 bg-rose-50 px-6 py-3 text-sm text-rose-800"
      role="alert"
      data-testid="error-banner"
    >
      <span>{message}</span>
      <button
        type="button"
        onClick={onDismiss}
        className="text-rose-600 hover:text-rose-800"
        data-testid="error-banner-dismiss"
      >
        Dismiss
      </button>
    </div>
  );
}
