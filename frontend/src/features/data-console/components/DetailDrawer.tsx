import React, { useEffect, useRef } from "react";
import { X } from "lucide-react";

type DetailDrawerProps = {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
}

export function DetailDrawer({ isOpen, onClose, title, children }: DetailDrawerProps) {
  const drawerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isOpen) {
        onClose();
      }
    };
    document.addEventListener("keydown", handleEscape);
    return () => { document.removeEventListener("keydown", handleEscape); };
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <>
      <div 
        className="fixed inset-0 bg-gray-500 bg-opacity-75 transition-opacity z-40" 
        onClick={onClose}
        aria-hidden="true"
      />
      <div className="fixed inset-y-0 right-0 flex max-w-full pl-10 z-50">
        <div 
          ref={drawerRef}
          className="w-screen max-w-md transform transition-transform duration-300 ease-in-out bg-white shadow-xl flex flex-col"
          role="dialog"
          aria-modal="true"
          aria-label={title}
        >
          <div className="px-4 py-6 sm:px-6 flex items-center justify-between border-b border-gray-200">
            <h2 className="text-lg font-medium text-gray-900">{title}</h2>
            <button
              type="button"
              className="rounded-md bg-white text-gray-400 hover:text-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500"
              onClick={onClose}
            >
              <span className="sr-only">Close panel</span>
              <X className="h-6 w-6" aria-hidden="true" />
            </button>
          </div>
          <div className="relative flex-1 px-4 py-6 sm:px-6 overflow-y-auto">
            {children}
          </div>
        </div>
      </div>
    </>
  );
}
