"use client";

import { useState, useEffect, Suspense, type FormEvent } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { api } from "@/lib/api";

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={null}>
      <ResetPasswordInner />
    </Suspense>
  );
}

function ResetPasswordInner() {
  const params = useSearchParams();
  const token = params.get("token") || params.get("reset_token") || "";
  const [pw1, setPw1] = useState("");
  const [pw2, setPw2] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!token) setError("No reset token in the URL. Open the link from your reset email.");
  }, [token]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (pw1.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    if (pw1 !== pw2) {
      setError("Passwords do not match.");
      return;
    }
    setLoading(true);
    try {
      await api("/auth/reset-confirm", {
        method: "POST",
        body: JSON.stringify({ token, new_password: pw1 }),
        skipAuth: true,
      });
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Reset failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4 bg-muted/30">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>Set a new password</CardTitle>
          <CardDescription>Choose a new password to finish the reset.</CardDescription>
        </CardHeader>
        <CardContent>
          {done ? (
            <div className="space-y-4">
              <Alert variant="success">Password updated. You can now sign in.</Alert>
              <Link href="/login/" className="block">
                <Button className="w-full">Go to sign in</Button>
              </Link>
            </div>
          ) : (
            <form onSubmit={onSubmit} className="space-y-4">
              {error && <Alert variant="destructive">{error}</Alert>}
              <div className="space-y-2">
                <Label htmlFor="pw1">New password</Label>
                <Input
                  id="pw1"
                  type="password"
                  autoComplete="new-password"
                  value={pw1}
                  onChange={(e) => setPw1(e.target.value)}
                  required
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="pw2">Confirm new password</Label>
                <Input
                  id="pw2"
                  type="password"
                  autoComplete="new-password"
                  value={pw2}
                  onChange={(e) => setPw2(e.target.value)}
                  required
                />
              </div>
              <Button type="submit" className="w-full" disabled={loading || !token}>
                {loading ? "Saving…" : "Set password"}
              </Button>
              <div className="text-center text-sm">
                <Link href="/login/" className="text-muted-foreground hover:text-primary underline-offset-4 hover:underline">
                  Back to sign in
                </Link>
              </div>
            </form>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
