"use client";

import { useEffect, useState, type FormEvent } from "react";
import { Trash2, Key, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert } from "@/components/ui/alert";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

type UserRow = {
  email: string;
  name?: string;
  role: string;
  created_at?: number;
};

export default function UsersPage() {
  const { user: me } = useAuth();
  const [users, setUsers] = useState<UserRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);

  // Create form state
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [pw, setPw] = useState("");
  const [role, setRole] = useState("user");
  const [creating, setCreating] = useState(false);

  // Per-row inline state
  const [resetFor, setResetFor] = useState<string | null>(null);
  const [resetPw, setResetPw] = useState("");
  const [busy, setBusy] = useState(false);

  async function refresh() {
    try {
      const list = await api<UserRow[]>("/auth/users");
      setUsers(list);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load users");
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setOkMsg(null);
    if (!email.trim() || pw.length < 8) {
      setError("Email + password (≥8 chars) required.");
      return;
    }
    setCreating(true);
    try {
      await api("/auth/register", {
        method: "POST",
        body: JSON.stringify({ email: email.trim(), name: name.trim(), password: pw, role }),
      });
      setOkMsg(`Created ${email}. Welcome email sent if SMTP is configured.`);
      setEmail("");
      setName("");
      setPw("");
      setRole("user");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Create failed");
    } finally {
      setCreating(false);
    }
  }

  async function onReset(emailTo: string) {
    if (resetPw.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    setBusy(true);
    setError(null);
    setOkMsg(null);
    try {
      await api(`/auth/users/${encodeURIComponent(emailTo)}/reset-password`, {
        method: "POST",
        body: JSON.stringify({ new_password: resetPw }),
      });
      setOkMsg(`Password reset for ${emailTo}. Tell the user out-of-band.`);
      setResetFor(null);
      setResetPw("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Reset failed");
    } finally {
      setBusy(false);
    }
  }

  async function onDelete(emailTo: string) {
    if (!confirm(`Delete ${emailTo} permanently?`)) return;
    setBusy(true);
    setError(null);
    setOkMsg(null);
    try {
      await api(`/auth/users/${encodeURIComponent(emailTo)}`, { method: "DELETE" });
      setOkMsg(`Deleted ${emailTo}`);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Users</h1>
        <p className="text-sm text-muted-foreground">Create accounts, reset passwords, and remove users.</p>
      </div>

      {error && <Alert variant="destructive">{error}</Alert>}
      {okMsg && <Alert variant="success">{okMsg}</Alert>}

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">➕ Create user</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={onCreate} className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Email</Label>
              <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
            </div>
            <div className="space-y-2">
              <Label>Name (optional)</Label>
              <Input value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="space-y-2">
              <Label>Initial password (≥8 chars)</Label>
              <Input type="password" value={pw} onChange={(e) => setPw(e.target.value)} required />
            </div>
            <div className="space-y-2">
              <Label>Role</Label>
              <select
                value={role}
                onChange={(e) => setRole(e.target.value)}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              >
                <option value="user">user</option>
                <option value="admin">admin</option>
              </select>
            </div>
            <div className="sm:col-span-2">
              <Button type="submit" disabled={creating}>
                {creating ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                Create user
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">📋 {users?.length ?? "—"} users</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {users === null ? (
            <p className="px-6 py-8 text-sm text-muted-foreground">Loading…</p>
          ) : users.length === 0 ? (
            <p className="px-6 py-8 text-sm text-muted-foreground">No users.</p>
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-muted/40 text-xs uppercase text-muted-foreground">
                <tr>
                  <th className="text-left font-medium px-4 py-2">Email</th>
                  <th className="text-left font-medium px-4 py-2">Name</th>
                  <th className="text-left font-medium px-4 py-2">Role</th>
                  <th className="text-right font-medium px-4 py-2">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {users.map((u) => {
                  const isMe = u.email === me?.email;
                  const isResetting = resetFor === u.email;
                  return (
                    <>
                      <tr key={u.email}>
                        <td className="px-4 py-3 font-medium">
                          {u.email}
                          {isMe && <span className="ml-2 text-xs text-muted-foreground">(you)</span>}
                        </td>
                        <td className="px-4 py-3 text-muted-foreground">{u.name || "—"}</td>
                        <td className="px-4 py-3">
                          <span className="text-xs px-1.5 py-0.5 rounded bg-secondary text-secondary-foreground uppercase">
                            {u.role}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-right space-x-2">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => {
                              setResetFor(isResetting ? null : u.email);
                              setResetPw("");
                            }}
                          >
                            <Key className="h-3.5 w-3.5" />
                            Reset
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => onDelete(u.email)}
                            disabled={isMe || busy}
                            title={isMe ? "Cannot delete yourself" : "Delete user"}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                            Delete
                          </Button>
                        </td>
                      </tr>
                      {isResetting && (
                        <tr key={u.email + "-reset"} className="bg-muted/20">
                          <td colSpan={4} className="px-4 py-3">
                            <div className="flex items-end gap-2 max-w-md">
                              <div className="flex-1 space-y-1">
                                <Label className="text-xs">New password (≥8 chars)</Label>
                                <Input
                                  type="password"
                                  value={resetPw}
                                  onChange={(e) => setResetPw(e.target.value)}
                                />
                              </div>
                              <Button onClick={() => onReset(u.email)} disabled={busy}>
                                Set
                              </Button>
                              <Button
                                variant="outline"
                                onClick={() => {
                                  setResetFor(null);
                                  setResetPw("");
                                }}
                              >
                                Cancel
                              </Button>
                            </div>
                          </td>
                        </tr>
                      )}
                    </>
                  );
                })}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
