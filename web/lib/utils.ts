import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// Mirrors backend policy in api_server.py:_check_password_strength.
// Returns null if valid, or an error string explaining the first failure.
export function validatePassword(pw: string): string | null {
  if (pw.length < 12) return "Password must be at least 12 characters.";
  const classes = [
    /[a-z]/.test(pw),
    /[A-Z]/.test(pw),
    /[0-9]/.test(pw),
    /[^A-Za-z0-9]/.test(pw),
  ].filter(Boolean).length;
  if (classes < 3) return "Password must include 3 of: lowercase, uppercase, digit, symbol.";
  return null;
}

export const PW_REQ_LABEL = "≥12 chars, 3 of: lower / UPPER / digit / symbol";
