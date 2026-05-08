"use client";

import { Suspense } from "react";
import { AdminShell } from "@/components/admin/admin-shell";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return (
    <Suspense fallback={null}>
      <AdminShell>{children}</AdminShell>
    </Suspense>
  );
}
